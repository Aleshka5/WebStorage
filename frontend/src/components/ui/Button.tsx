import type { ButtonHTMLAttributes } from "react";

export type ButtonVariant = "primary" | "secondary" | "danger";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  isLoading?: boolean;
}

const variantClasses: Record<ButtonVariant, string> = {
  primary:
    "bg-sky-600 text-white hover:bg-sky-500 focus-visible:ring-sky-500 disabled:bg-sky-800 disabled:text-sky-300",
  secondary:
    "bg-zinc-700 text-zinc-100 hover:bg-zinc-600 focus-visible:ring-zinc-500 disabled:bg-zinc-800 disabled:text-zinc-500",
  danger:
    "bg-red-600 text-white hover:bg-red-500 focus-visible:ring-red-500 disabled:bg-red-900 disabled:text-red-300",
};

export function Button({
  variant = "primary",
  isLoading = false,
  disabled,
  className = "",
  type = "button",
  children,
  ...props
}: ButtonProps) {
  const isDisabled = disabled || isLoading;

  return (
    <button
      type={type}
      disabled={isDisabled}
      className={[
        "inline-flex w-full items-center justify-center rounded-lg px-4 py-2.5 text-sm font-medium transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-900",
        "disabled:cursor-not-allowed",
        variantClasses[variant],
        className,
      ].join(" ")}
      {...props}
    >
      {isLoading ? (
        <span className="inline-flex items-center gap-2">
          <span className="h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
          Загрузка...
        </span>
      ) : (
        children
      )}
    </button>
  );
}
