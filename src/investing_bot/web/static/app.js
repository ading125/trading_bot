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
