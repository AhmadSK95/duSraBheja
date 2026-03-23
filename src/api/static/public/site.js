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

  // ── Decision slider (case study pages) ──
  document.querySelectorAll("[data-decision-slider]").forEach(function (slider) {
    var slides = slider.querySelectorAll(".cs-decision-slide");
    var counter = slider.querySelector("[data-slider-counter]");
    var prevBtn = slider.querySelector("[data-slider-prev]");
    var nextBtn = slider.querySelector("[data-slider-next]");
    if (slides.length === 0) return;
    var current = 0;
    slides[0].classList.add("is-active");

    function showSlide(idx) {
      slides[current].classList.remove("is-active");
      current = (idx + slides.length) % slides.length;
      slides[current].classList.add("is-active");
      if (counter) counter.textContent = (current + 1) + " / " + slides.length;
    }

    if (prevBtn) prevBtn.addEventListener("click", function () { showSlide(current - 1); });
    if (nextBtn) nextBtn.addEventListener("click", function () { showSlide(current + 1); });
  });

  // ── Starter prompt chips ──
  const form = document.querySelector("[data-public-chat-form]");
  document.querySelectorAll("[data-starter-prompt]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var field = form ? form.querySelector("textarea[name='question']") : null;
      if (field) {
        field.value = btn.dataset.starterPrompt;
        field.focus();
      }
    });
  });

  // ── Chat (Open Brain) ──
  if (!form) return;

  const log = document.querySelector("[data-public-chat-log]");
  const status = document.querySelector("[data-public-chat-status]");
  const submit = form.querySelector("button[type='submit']");
  const newConvBtn = document.querySelector("[data-new-conversation]");
  const chatEnabled =
    typeof pageData.chatEnabled === "boolean" ? pageData.chatEnabled : true;
  const captchaEnabled =
    typeof pageData.captchaEnabled === "boolean"
      ? pageData.captchaEnabled
      : !!pageData.turnstileConfigured;

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

  if (submit && !chatEnabled) {
    submit.disabled = true;
  }

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    if (!chatEnabled) return;
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
      };
      if (captchaEnabled) {
        body.turnstile_token = turnstileField ? turnstileField.value : "";
      }
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
      submit.disabled = !chatEnabled;
      if (status) {
        const remaining = !chatEnabled
          ? "The public clone is temporarily offline."
          : captchaEnabled
          ? "Multi-turn conversation. Ask follow-ups."
          : "Multi-turn conversation. Captcha is disabled for this public session.";
        status.textContent = remaining;
      }
    }
  });
})();
