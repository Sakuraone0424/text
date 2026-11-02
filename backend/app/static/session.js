"use strict";
/* One sign-in for the whole site: every page uses the same /auth/login and the same session cookie
   (name ustc_quiz_session, path /, 30 days). A sub-page only asks for the password when the server says
   401 - never because some other request failed. */
(() => {
  const mount = (slot) => {
    const form = document.createElement("form");
    form.id = "login";
    form.className = "card login-card";
    form.innerHTML =
      '<h2>登录</h2><label for="password" class="label">登录密码</label>' +
      '<input id="password" type="password" autocomplete="current-password" required />' +
      '<p id="login-error" class="note warn" role="alert" hidden></p>' +
      '<button class="primary" type="submit">登录</button>' +
      '<p class="note">与主应用共用同一个登录，登录一次即可。</p>';
    slot.replaceChildren(form);
    return form;
  };
  async function check() {
    const response = await fetch("/auth/me", { credentials: "same-origin", cache: "no-store" });
    if (response.status === 401) return false;
    if (!response.ok) throw new Error("读取登录状态失败（" + response.status + "）");
    return true;
  }
  /* Resolves once the visitor is signed in (immediately when the cookie is already valid). */
  async function ensure(slot) {
    if (await check()) {
      slot.replaceChildren();
      return;
    }
    const form = mount(slot);
    await new Promise((resolve) => {
      form.onsubmit = async (event) => {
        event.preventDefault();
        const error = form.querySelector("#login-error");
        error.hidden = true;
        try {
          const response = await fetch("/auth/login", {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ password: form.querySelector("#password").value }),
          });
          if (!response.ok) throw new Error(response.status === 401 ? "密码不对" : "登录失败（" + response.status + "）");
          slot.replaceChildren();
          resolve();
        } catch (e) {
          error.textContent = e.message;
          error.hidden = false;
        }
      };
    });
  }
  window.Session = { ensure, check };
})();
