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
      <div className={["field", error ? "is-invalid" : "", className].filter(Boolean).join(" ")}>
        <label htmlFor={inputId}>{label}</label>
        <input ref={ref} id={inputId} {...props} />
        {error && <p className="form-error">{error}</p>}
      </div>
    );
  },
);

Input.displayName = "Input";
