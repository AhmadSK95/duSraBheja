(function () {
  const dataNode = document.getElementById("public-page-data");
  let pageData = {};
  try {
    pageData = dataNode ? JSON.parse(dataNode.textContent || "{}") : {};
  } catch (_error) {
    pageData = {};
  }

  const form = document.querySelector("[data-public-chat-form]");
  if (!form) {
    return;
  }

  const log = document.querySelector("[data-public-chat-log]");
  const status = document.querySelector("[data-public-chat-status]");
  const submit = form.querySelector("button[type='submit']");

  const appendMessage = (speaker, content) => {
    if (!log) return;
    const wrapper = document.createElement("div");
    wrapper.className = "public-chat-message";
    wrapper.innerHTML = `<strong>${speaker}</strong><div>${content}</div>`;
    log.appendChild(wrapper);
    log.scrollTop = log.scrollHeight;
  };

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const questionField = form.querySelector("textarea[name='question']");
    const turnstileField = form.querySelector("input[name='turnstile_token']");
    const question = questionField?.value?.trim();
    if (!question) {
      return;
    }
    submit.disabled = true;
    if (status) status.textContent = "Thinking…";
    appendMessage("You", question.replace(/\n/g, "<br />"));
    try {
      const response = await fetch("/api/public/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question,
          turnstile_token: turnstileField?.value || "",
        }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) {
        appendMessage("Open Brain", payload.detail || payload.error || "I couldn’t answer that right now.");
      } else {
        appendMessage("Open Brain", (payload.answer || "").replace(/\n/g, "<br />"));
      }
    } catch (_error) {
      appendMessage("Open Brain", "The public profile chat is temporarily unavailable.");
    } finally {
      submit.disabled = false;
      if (status) status.textContent = pageData.turnstileConfigured
        ? "Public profile chat is limited to questions about Ahmad, his work, and collaboration."
        : "Turnstile isn’t configured yet, so chat will stay locked until that security step is enabled.";
      if (questionField) questionField.value = "";
    }
  });
})();
