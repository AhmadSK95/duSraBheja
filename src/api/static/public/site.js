(function () {
  "use strict";

  // ── Page data ──
  const dataNode = document.getElementById("public-page-data");
  let pageData = {};
  try {
    pageData = dataNode ? JSON.parse(dataNode.textContent || "{}") : {};
  } catch (_e) {
    pageData = {};
  }

  // ── Mobile nav toggle ──
  const navToggle = document.querySelector("[data-nav-toggle]");
  const nav = document.querySelector("[data-nav]");
  if (navToggle && nav) {
    navToggle.addEventListener("click", function () {
      nav.classList.toggle("is-open");
    });
    document.addEventListener("click", function (e) {
      if (!nav.contains(e.target) && !navToggle.contains(e.target)) {
        nav.classList.remove("is-open");
      }
    });
  }

  // ── Scroll reveal (IntersectionObserver fallback) ──
  const supportsScrollTimeline =
    CSS.supports && CSS.supports("animation-timeline", "view()");
  if (!supportsScrollTimeline) {
    const revealElements = document.querySelectorAll(".reveal");
    if (revealElements.length > 0 && "IntersectionObserver" in window) {
      const observer = new IntersectionObserver(
        function (entries) {
          entries.forEach(function (entry) {
            if (entry.isIntersecting) {
              entry.target.classList.add("is-visible");
              observer.unobserve(entry.target);
            }
          });
        },
        { threshold: 0.1, rootMargin: "0px 0px -40px 0px" }
      );
      revealElements.forEach(function (el) {
        observer.observe(el);
      });
    } else {
      // No IntersectionObserver — just show everything
      revealElements.forEach(function (el) {
        el.classList.add("is-visible");
      });
    }
  }

  // ── Chat (Open Brain) ──
  const form = document.querySelector("[data-public-chat-form]");
  if (!form) return;

  const log = document.querySelector("[data-public-chat-log]");
  const status = document.querySelector("[data-public-chat-status]");
  const submit = form.querySelector("button[type='submit']");
  const newConvBtn = document.querySelector("[data-new-conversation]");

  let conversationId = null;

  function appendMessage(speaker, content, isUser) {
    if (!log) return;
    const wrapper = document.createElement("div");
    wrapper.className =
      "chat-message" + (isUser ? " chat-message--user" : "");
    wrapper.innerHTML =
      "<strong>" + speaker + "</strong><div>" + content + "</div>";
    log.appendChild(wrapper);
    log.scrollTop = log.scrollHeight;
  }

  function resetConversation() {
    conversationId = null;
    if (log) {
      log.innerHTML =
        '<div class="chat-message"><strong>Ahmad\'s Clone</strong><div>New conversation started. Ask me anything about Ahmad\'s work, projects, or collaboration fit.</div></div>';
    }
  }

  if (newConvBtn) {
    newConvBtn.addEventListener("click", resetConversation);
  }

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    const questionField = form.querySelector("textarea[name='question']");
    const turnstileField = form.querySelector(
      "input[name='turnstile_token']"
    );
    const question = questionField ? questionField.value.trim() : "";
    if (!question) return;

    submit.disabled = true;
    if (status) status.textContent = "Thinking\u2026";
    appendMessage("You", question.replace(/\n/g, "<br />"), true);
    if (questionField) questionField.value = "";

    try {
      const body = {
        question: question,
        turnstile_token: turnstileField ? turnstileField.value : "",
      };
      if (conversationId) {
        body.conversation_id = conversationId;
      }

      const response = await fetch("/api/public/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = await response.json();

      if (!response.ok || !payload.ok) {
        appendMessage(
          "Ahmad's Clone",
          payload.detail || payload.error || "I couldn't answer that right now.",
          false
        );
      } else {
        appendMessage(
          "Ahmad's Clone",
          (payload.answer || "").replace(/\n/g, "<br />"),
          false
        );
        if (payload.conversation_id) {
          conversationId = payload.conversation_id;
        }
      }
    } catch (_err) {
      appendMessage(
        "Ahmad's Clone",
        "The public profile chat is temporarily unavailable.",
        false
      );
    } finally {
      submit.disabled = false;
      if (status) {
        const remaining =
          typeof pageData.turnstileConfigured !== "undefined" &&
          pageData.turnstileConfigured
            ? "Multi-turn conversation. Ask follow-ups."
            : "Turnstile isn\u2019t configured yet, so chat will stay locked.";
        status.textContent = remaining;
      }
    }
  });
})();
