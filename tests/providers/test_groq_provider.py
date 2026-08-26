from __future__ import annotations

from datetime import UTC, datetime
import json

import httpx
import pytest

from investing_bot.models import (
    AnalysisDecision,
    EvidenceItem,
    PolicyRelevance,
    ProviderCapability,
    StructuredAnalysisRequest,
)
from investing_bot.providers import (
    CapabilitySelection,
    CredentialPresenceStore,
    ProviderConfiguration,
    ProviderManager,
    ProviderRegistry,
    ProviderTarget,
    build_default_registry,
    default_provider_configuration,
)
from investing_bot.providers.contracts import (
    ProviderCallError,
    ProviderErrorCode,
    ProviderHealthState,
)
from investing_bot.providers.groq import (
    GroqStructuredLLMProvider,
    build_groq_manifest,
)


NOW = datetime(2026, 8, 20, 14, 0, tzinfo=UTC)
API_KEY = "test-provider-key-that-must-never-leak"


async def _no_sleep() -> None:
    return None


class FakeCredentialStore:
    @property
    def unlocked(self) -> bool:
        return True

    def has_reference(self, reference: str) -> bool:
        return reference == "cred_groq"

    def is_configured(self, reference: str) -> bool:
        return self.has_reference(reference)

    def get_secret(self, reference: str) -> str:
        if reference != "cred_groq":
            raise AssertionError("unexpected credential reference")
        return API_KEY


def analysis_request() -> StructuredAnalysisRequest:
    return StructuredAnalysisRequest(
        ticker="CVX",
        prompt_version="growth_analysis.v1",
        output_schema_version="structured_analysis.v1",
        evidence=(
            EvidenceItem(
                source_id="news-cvx-1",
                text="Chevron published a production outlook update.",
                observed_at=NOW,
            ),
        ),
    )


def successful_completion(*, source_ids: list[str] | None = None) -> dict:
    content = {
        "ticker": "CVX",
        "decision": "investigate",
        "growth_score": 68,
        "evidence_quality": 72,
        "policy_relevance": "low",
        "catalysts": ["Production outlook execution"],
        "earnings_assessment": "No earnings record was supplied.",
        "bullish_thesis": "The supplied outlook could support growth.",
        "bearish_case": "Execution details remain limited.",
        "risks": ["Outlook execution risk"],
        "uncertainties": ["No financial results were supplied"],
        "source_ids": source_ids or ["news-cvx-1"],
    }
    return {
        "id": "chatcmpl-test",
        "choices": [
            {
                "message": {"content": json.dumps(content)},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 400,
            "completion_tokens": 120,
            "total_tokens": 520,
        },
        "x_groq": {"id": "req-test"},
    }


@pytest.mark.anyio
async def test_health_and_analysis_use_vault_reference_and_strict_schema() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["Authorization"] == f"Bearer {API_KEY}"
        headers = {
            "x-ratelimit-limit-requests": "30",
            "x-ratelimit-remaining-requests": "29",
        }
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"data": [{"id": "openai/gpt-oss-120b"}]},
                headers=headers,
            )
        payload = json.loads(request.content)
        assert payload["model"] == "openai/gpt-oss-120b"
        assert "Never put opaque source IDs" in payload["messages"][0]["content"]
        assert payload["response_format"]["type"] == "json_schema"
        assert payload["response_format"]["json_schema"]["strict"] is True
        assert (
            payload["response_format"]["json_schema"]["schema"]["properties"]
            ["source_ids"]["minItems"]
            == 1
        )
        assert API_KEY not in request.content.decode()
        return httpx.Response(200, json=successful_completion(), headers=headers)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = GroqStructuredLLMProvider(
            FakeCredentialStore(), client=client, retries=0, now=lambda: NOW
        )
        health = await provider.test_connection(
            ProviderCapability.STRUCTURED_LLM, "cred_groq"
        )
        result = await provider.analyze(analysis_request(), "cred_groq")

    assert health.state is ProviderHealthState.HEALTHY
    assert health.quota is not None
    assert health.quota.remaining == 29
    assert result.items[0].decision is AnalysisDecision.INVESTIGATE
    assert result.items[0].policy_relevance is PolicyRelevance.LOW
    assert result.items[0].source_ids == ("news-cvx-1",)
    assert result.provenance.request_id == "req-test"
    assert result.usage is not None
    assert result.usage.total_tokens == 520
    assert [request.url.path for request in requests] == [
        "/openai/v1/models",
        "/openai/v1/chat/completions",
    ]


@pytest.mark.anyio
async def test_analysis_request_bounds_large_evidence_payload() -> None:
    captured: list[tuple[dict, int]] = []
    evidence = tuple(
        EvidenceItem(
            source_id=f"source-{index:02d}-" + ("s" * 50),
            text=f"Evidence item {index}: " + ("x" * 2_000),
            observed_at=NOW,
        )
        for index in range(40)
    )
    request_model = StructuredAnalysisRequest(
        ticker="CVX",
        prompt_version="growth_analysis.v1",
        output_schema_version="structured_analysis.v1",
        evidence=evidence,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        prompt = json.loads(payload["messages"][1]["content"])
        captured.append((prompt, len(request.content)))
        return httpx.Response(
            200,
            json=successful_completion(
                source_ids=[prompt["evidence"][0]["source_id"]]
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = GroqStructuredLLMProvider(
            FakeCredentialStore(), client=client, retries=0, now=lambda: NOW
        )
        await provider.analyze(request_model, "cred_groq")

    prompt, request_bytes = captured[0]
    assert len(prompt["evidence"]) == 4
    assert sum(len(item["text"]) for item in prompt["evidence"]) == 4_000
    assert max(len(item["text"]) for item in prompt["evidence"]) == 1_000
    assert request_bytes < 14_000


@pytest.mark.anyio
async def test_manager_forwards_credential_reference_and_stamps_run() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(
                200, json={"data": [{"id": "openai/gpt-oss-120b"}]}
            )
        return httpx.Response(200, json=successful_completion())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        credentials = FakeCredentialStore()
        provider = GroqStructuredLLMProvider(credentials, client=client, retries=0)
        registry = ProviderRegistry()
        registry.register(build_groq_manifest(), lambda: provider)
        manager = ProviderManager(
            registry=registry,
            configuration=ProviderConfiguration(
                selections={
                    ProviderCapability.STRUCTURED_LLM: CapabilitySelection(
                        primary=ProviderTarget(
                            provider_id="groq", credential_ref="cred_groq"
                        )
                    )
                }
            ),
            credentials=credentials,
        )

        pinned = await manager.pin(
            ProviderCapability.STRUCTURED_LLM, run_id="groq-analysis-run"
        )
        result = await pinned.analyze(analysis_request())

    assert pinned.provider_id == "groq"
    assert result.provenance.run_id == "groq-analysis-run"
    assert result.provenance.configuration_hash is not None


@pytest.mark.anyio
async def test_locked_default_runtime_falls_back_without_network_access() -> None:
    credentials = CredentialPresenceStore()
    manager = ProviderManager(
        registry=build_default_registry(credentials=credentials),
        configuration=default_provider_configuration(live_analysis=True),
        credentials=credentials,
    )

    pinned = await manager.pin(
        ProviderCapability.STRUCTURED_LLM, run_id="locked-groq-run"
    )
    result = await pinned.analyze(analysis_request())

    assert pinned.provider_id == "fixture_recorded"
    assert len(result.provenance.fallback_attempts) == 1
    assert result.provenance.fallback_attempts[0].provider_id == "groq"
    assert result.provenance.fallback_attempts[0].error_code == "authentication"


@pytest.mark.anyio
async def test_authentication_failure_is_sanitized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": {"message": f"bad key {API_KEY}"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = GroqStructuredLLMProvider(
            FakeCredentialStore(), client=client, retries=0
        )
        with pytest.raises(ProviderCallError) as caught:
            await provider.analyze(analysis_request(), "cred_groq")

    assert caught.value.failure.code is ProviderErrorCode.AUTHENTICATION
    assert API_KEY not in str(caught.value)
    assert API_KEY not in caught.value.failure.model_dump_json()


@pytest.mark.anyio
async def test_structured_generation_400_is_retried_without_retaining_body() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "type": "invalid_request_error",
                        "message": "Generated JSON does not match the expected schema.",
                        "failed_generation": {"attempted": "private model output"},
                    }
                },
            )
        return httpx.Response(200, json=successful_completion())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = GroqStructuredLLMProvider(
            FakeCredentialStore(),
            client=client,
            retries=1,
            sleep=lambda _: _no_sleep(),
            now=lambda: NOW,
        )
        result = await provider.analyze(analysis_request(), "cred_groq")

    assert calls == 2
    assert result.items[0].ticker == "CVX"


@pytest.mark.anyio
async def test_unknown_citation_rejects_provider_output() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=successful_completion(source_ids=["unknown-source"]),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = GroqStructuredLLMProvider(
            FakeCredentialStore(), client=client, retries=0
        )
        with pytest.raises(ProviderCallError) as caught:
            await provider.analyze(analysis_request(), "cred_groq")

    assert caught.value.failure.code is ProviderErrorCode.SCHEMA_INCOMPATIBLE
    assert caught.value.failure.retryable is False
