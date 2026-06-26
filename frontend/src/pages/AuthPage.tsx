import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button } from "../components/ui/Button";
import { Input } from "../components/ui/Input";
import { AuthError, useAuthStore } from "../store/auth";
import {
  validateEmail,
  validatePasswordMatch,
  validateRequired,
} from "../utils/validation";

type AuthTab = "login" | "register";

interface LoginForm {
  email: string;
  password: string;
}

interface RegisterForm {
  email: string;
  password: string;
  confirmPassword: string;
}

interface FormErrors {
  email?: string;
  password?: string;
  confirmPassword?: string;
}

export default function AuthPage() {
  const navigate = useNavigate();
  const { login, register, isLoading } = useAuthStore();

  const [activeTab, setActiveTab] = useState<AuthTab>("login");
  const [loginForm, setLoginForm] = useState<LoginForm>({ email: "", password: "" });
  const [registerForm, setRegisterForm] = useState<RegisterForm>({
    email: "",
    password: "",
    confirmPassword: "",
  });
  const [errors, setErrors] = useState<FormErrors>({});

  const resetErrors = () => setErrors({});

  const switchTab = (tab: AuthTab) => {
    setActiveTab(tab);
    resetErrors();
  };

  const validateLoginForm = (): FormErrors => {
    const nextErrors: FormErrors = {};
    const emailError = validateEmail(loginForm.email);
    const passwordError = validateRequired(loginForm.password, "Введите пароль");

    if (emailError) nextErrors.email = emailError;
    if (passwordError) nextErrors.password = passwordError;

    return nextErrors;
  };

  const validateRegisterForm = (): FormErrors => {
    const nextErrors: FormErrors = {};
    const emailError = validateEmail(registerForm.email);
    const passwordError = validateRequired(registerForm.password, "Введите пароль");
    const confirmError =
      validateRequired(registerForm.confirmPassword, "Подтвердите пароль") ??
      validatePasswordMatch(registerForm.password, registerForm.confirmPassword);

    if (emailError) nextErrors.email = emailError;
    if (passwordError) nextErrors.password = passwordError;
    if (confirmError) nextErrors.confirmPassword = confirmError;

    return nextErrors;
  };

  const applyAuthError = (error: unknown): void => {
    if (error instanceof AuthError && error.field) {
      setErrors({ [error.field]: error.message });
      return;
    }

    if (error instanceof AuthError) {
      setErrors({ password: error.message });
    }
  };

  const handleLoginSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const nextErrors = validateLoginForm();
    setErrors(nextErrors);

    if (Object.keys(nextErrors).length > 0) {
      return;
    }

    try {
      await login(loginForm.email.trim(), loginForm.password);
      navigate("/files", { replace: true });
    } catch (error) {
      applyAuthError(error);
    }
  };

  const handleRegisterSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const nextErrors = validateRegisterForm();
    setErrors(nextErrors);

    if (Object.keys(nextErrors).length > 0) {
      return;
    }

    try {
      await register(registerForm.email.trim(), registerForm.password);
      navigate("/files", { replace: true });
    } catch (error) {
      applyAuthError(error);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-zinc-950 px-4 py-10">
      <div className="w-full max-w-md rounded-2xl border border-zinc-800 bg-zinc-900/80 p-8 shadow-xl backdrop-blur">
        <header className="mb-8 text-center">
          <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-xl bg-sky-600/20 text-sky-400">
            <svg
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 24 24"
              fill="currentColor"
              className="h-6 w-6"
              aria-hidden="true"
            >
              <path d="M4.5 6.375a4.125 4.125 0 118.25 0 4.125 4.125 0 01-8.25 0zM14.25 8.625a3.375 3.375 0 116.75 0 3.375 3.375 0 01-6.75 0zM1.5 19.125a7.125 7.125 0 0114.25 0v.003l-.001.119a.75.75 0 01-.363.63 13.067 13.067 0 01-6.761 1.873c-2.472 0-4.786-.343-6.762-1.873a.75.75 0 01-.364-.63l-.001-.122zM17.25 19.128l-.001.144a2.25 2.25 0 01-.233.96 10.088 10.088 0 005.06-1.01.75.75 0 00.42-.643 4.875 4.875 0 00-6.957-4.611 8.586 8.586 0 011.71 5.157v.003z" />
            </svg>
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-zinc-100">HomeCloud</h1>
          <p className="mt-1 text-sm text-zinc-400">Сетевое хранилище для дома</p>
        </header>

        <nav className="mb-6 flex rounded-lg bg-zinc-800/60 p-1">
          <button
            type="button"
            onClick={() => switchTab("login")}
            className={[
              "flex-1 rounded-md py-2 text-sm font-medium transition-colors",
              activeTab === "login"
                ? "bg-zinc-700 text-zinc-100 shadow-sm"
                : "text-zinc-400 hover:text-zinc-200",
            ].join(" ")}
          >
            Вход
          </button>
          <button
            type="button"
            onClick={() => switchTab("register")}
            className={[
              "flex-1 rounded-md py-2 text-sm font-medium transition-colors",
              activeTab === "register"
                ? "bg-zinc-700 text-zinc-100 shadow-sm"
                : "text-zinc-400 hover:text-zinc-200",
            ].join(" ")}
          >
            Регистрация
          </button>
        </nav>

        {activeTab === "login" ? (
          <form className="flex flex-col gap-4" onSubmit={handleLoginSubmit} noValidate>
            <Input
              label="Email"
              type="email"
              autoComplete="email"
              value={loginForm.email}
              onChange={(event) => setLoginForm({ ...loginForm, email: event.target.value })}
              error={errors.email}
              disabled={isLoading}
            />
            <Input
              label="Пароль"
              type="password"
              autoComplete="current-password"
              value={loginForm.password}
              onChange={(event) => setLoginForm({ ...loginForm, password: event.target.value })}
              error={errors.password}
              disabled={isLoading}
            />
            <Button type="submit" isLoading={isLoading} className="mt-2">
              Войти
            </Button>
            <Button type="button" variant="secondary" disabled className="opacity-60">
              <span className="inline-flex items-center gap-2">
                <svg viewBox="0 0 24 24" className="h-4 w-4" aria-hidden="true">
                  <path
                    fill="currentColor"
                    d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 01-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"
                  />
                  <path
                    fill="currentColor"
                    d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
                  />
                  <path
                    fill="currentColor"
                    d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l3.66-2.84z"
                  />
                  <path
                    fill="currentColor"
                    d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
                  />
                </svg>
                Войти через Google
              </span>
            </Button>
          </form>
        ) : (
          <form className="flex flex-col gap-4" onSubmit={handleRegisterSubmit} noValidate>
            <Input
              label="Email"
              type="email"
              autoComplete="email"
              value={registerForm.email}
              onChange={(event) =>
                setRegisterForm({ ...registerForm, email: event.target.value })
              }
              error={errors.email}
              disabled={isLoading}
            />
            <Input
              label="Пароль"
              type="password"
              autoComplete="new-password"
              value={registerForm.password}
              onChange={(event) =>
                setRegisterForm({ ...registerForm, password: event.target.value })
              }
              error={errors.password}
              disabled={isLoading}
            />
            <Input
              label="Подтверждение пароля"
              type="password"
              autoComplete="new-password"
              value={registerForm.confirmPassword}
              onChange={(event) =>
                setRegisterForm({ ...registerForm, confirmPassword: event.target.value })
              }
              error={errors.confirmPassword}
              disabled={isLoading}
            />
            <Button type="submit" isLoading={isLoading} className="mt-2">
              Создать аккаунт
            </Button>
          </form>
        )}
      </div>
    </div>
  );
}
