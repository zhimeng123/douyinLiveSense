const form = document.getElementById("login-form");
const password = document.getElementById("login-password");
const submit = document.getElementById("login-submit");
const error = document.getElementById("login-error");

function showError(message) {
  error.textContent = message || "";
  error.hidden = !message;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  submit.disabled = true;
  showError("");
  try {
    const response = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: password.value }),
    });
    const body = await response.json();
    if (response.ok) {
      location.replace("/");
      return;
    }
    if (response.status === 429) {
      showError(`尝试次数过多，请 ${body.detail?.match(/\d+/)?.[0] || "稍后"} 秒后再试`);
    } else if (response.status === 401) {
      showError("密码错误");
    } else {
      showError(body.detail || "登录失败，请稍后重试");
    }
  } catch (_error) {
    showError("无法连接服务，请确认服务正在运行");
  } finally {
    submit.disabled = false;
  }
});
