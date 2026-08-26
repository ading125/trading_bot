"use strict";

const drawerButtons = document.querySelectorAll("[data-drawer-target]");
const drawers = document.querySelectorAll("dialog.drawer");

for (const button of drawerButtons) {
  button.addEventListener("click", () => {
    const drawer = document.getElementById(button.dataset.drawerTarget);
    if (drawer instanceof HTMLDialogElement) {
      drawer.showModal();
    }
  });
}

for (const drawer of drawers) {
  const closeButton = drawer.querySelector("[data-drawer-close]");
  closeButton?.addEventListener("click", () => drawer.close());

  drawer.addEventListener("click", (event) => {
    const bounds = drawer.getBoundingClientRect();
    const inside =
      event.clientX >= bounds.left &&
      event.clientX <= bounds.right &&
      event.clientY >= bounds.top &&
      event.clientY <= bounds.bottom;
    if (!inside) {
      drawer.close();
    }
  });
}

const analysisTabs = Array.from(
  document.querySelectorAll("[data-analysis-target]")
);

function activateAnalysis(tab) {
  const targetId = tab.dataset.analysisTarget;
  if (!targetId) return;
  for (const item of analysisTabs) {
    const selected = item === tab;
    item.classList.toggle("is-active", selected);
    item.setAttribute("aria-selected", String(selected));
    item.tabIndex = selected ? 0 : -1;
    const panelId = item.dataset.analysisTarget;
    const panel = panelId ? document.getElementById(panelId) : null;
    if (panel) panel.hidden = !selected;
  }
}

for (const [index, tab] of analysisTabs.entries()) {
  tab.addEventListener("mouseenter", () => activateAnalysis(tab));
  tab.addEventListener("focus", () => activateAnalysis(tab));
  tab.addEventListener("click", () => activateAnalysis(tab));
  tab.addEventListener("keydown", (event) => {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    let nextIndex = index;
    if (event.key === "ArrowDown") nextIndex = (index + 1) % analysisTabs.length;
    if (event.key === "ArrowUp") {
      nextIndex = (index - 1 + analysisTabs.length) % analysisTabs.length;
    }
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = analysisTabs.length - 1;
    activateAnalysis(analysisTabs[nextIndex]);
    analysisTabs[nextIndex].focus();
  });
}
