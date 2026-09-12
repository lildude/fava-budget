function bindBudgetCopyButtons() {
  document.querySelectorAll("[data-budget-directive]").forEach((button) => {
    if (!(button instanceof HTMLButtonElement) || button.dataset.bound) return;

    button.dataset.bound = "true";
    button.addEventListener("click", async () => {
      const directive = button.dataset.budgetDirective;
      if (!directive) return;

      const originalLabel = button.textContent;
      try {
        await navigator.clipboard.writeText(directive);
        button.textContent = "Copied";
      } catch (error) {
        console.error("Could not copy budget directive", error);
        button.textContent = "Copy failed";
      }
      window.setTimeout(() => {
        button.textContent = originalLabel;
      }, 1800);
    });
  });
}

export default {
  onExtensionPageLoad() {
    bindBudgetCopyButtons();
  },
};
