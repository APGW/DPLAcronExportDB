const form = document.querySelector("#login-form");
const submit = document.querySelector("#login-submit");
const errorMessage = document.querySelector("#login-error");

async function sessionState() {
  const response = await fetch("/api/auth/session", { cache: "no-store" });
  return response.json();
}

async function login(event) {
  event.preventDefault();
  errorMessage.hidden = true;
  submit.disabled = true;
  submit.textContent = "Anmeldung wird geprüft …";
  try {
    const response = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: document.querySelector("#login-username").value,
        password: document.querySelector("#login-password").value,
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || "Anmeldung fehlgeschlagen.");
    window.location.replace("/");
  } catch (error) {
    errorMessage.textContent = error.message;
    errorMessage.hidden = false;
    document.querySelector("#login-password").value = "";
    document.querySelector("#login-password").focus();
  } finally {
    submit.disabled = false;
    submit.textContent = "Anmelden";
  }
}

form.addEventListener("submit", login);
sessionState()
  .then((session) => {
    if (session.authenticated) window.location.replace("/");
  })
  .catch(() => {
    errorMessage.textContent = "Der Dienst ist derzeit nicht erreichbar.";
    errorMessage.hidden = false;
  });
