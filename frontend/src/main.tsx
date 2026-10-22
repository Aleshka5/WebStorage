import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Toaster } from "react-hot-toast";
import { AppRouter } from "./router";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AppRouter />
    <Toaster
      position="bottom-right"
      toastOptions={{
        className: "banner",
        style: {
          background: "var(--paper-raised)",
          color: "var(--ink)",
          border: "1px solid var(--rule)",
          borderRadius: 0,
          boxShadow: "none",
          fontFamily: "var(--sans)",
        },
        success: {
          iconTheme: {
            primary: "var(--accent-text)",
            secondary: "var(--paper-raised)",
          },
        },
        error: {
          style: {
            background: "var(--danger-wash)",
            color: "var(--danger)",
            border: "1px solid var(--danger)",
            borderRadius: 0,
            boxShadow: "none",
          },
          iconTheme: {
            primary: "var(--danger)",
            secondary: "var(--danger-wash)",
          },
        },
      }}
    />
  </StrictMode>,
);
