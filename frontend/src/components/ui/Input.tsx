import type { InputHTMLAttributes } from "react";
import { forwardRef } from "react";

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  error?: string;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ label, error, id, className = "", ...props }, ref) => {
    const inputId = id ?? label.toLowerCase().replace(/\s+/g, "-");

    return (
      <div className="flex flex-col gap-1.5">
        <label htmlFor={inputId} className="text-sm font-medium text-zinc-300">
          {label}
        </label>
        <input
          ref={ref}
          id={inputId}
          className={[
            "w-full rounded-lg border bg-zinc-800/80 px-3 py-2.5 text-sm text-zinc-100 placeholder:text-zinc-500",
            "transition-colors focus:outline-none focus:ring-2 focus:ring-sky-500/50",
            error ? "border-red-500/70" : "border-zinc-700 hover:border-zinc-600",
            className,
          ].join(" ")}
          {...props}
        />
        {error && <p className="text-xs text-red-400">{error}</p>}
      </div>
    );
  },
);

Input.displayName = "Input";
