// Small progressive enhancements. Every page works without JavaScript.
document.addEventListener("DOMContentLoaded", () => {
  // Show / hide password
  document.querySelectorAll("[data-toggle-password]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const input = btn.parentElement.querySelector("input");
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      btn.textContent = show ? "Hide" : "Show";
      btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
    });
  });

  // Caps Lock warning on password fields
  const capsHint = document.querySelector("[data-caps-hint]");
  if (capsHint) {
    document.querySelectorAll('input[type="password"]').forEach((input) => {
      input.addEventListener("keyup", (e) => {
        capsHint.hidden = !(e.getModifierState && e.getModifierState("CapsLock"));
      });
    });
  }

  // Copy API key
  document.querySelectorAll("[data-copy]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const text = document.querySelector(btn.dataset.copy).textContent.trim();
      try {
        await navigator.clipboard.writeText(text);
        btn.textContent = "Copied";
      } catch {
        const range = document.createRange();
        range.selectNodeContents(document.querySelector(btn.dataset.copy));
        const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
        btn.textContent = "Selected, press Ctrl+C";
      }
    });
  });

  // Confirm destructive actions
  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (e) => {
      if (!window.confirm(form.dataset.confirm)) e.preventDefault();
    });
  });
});
