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
        className: "text-sm",
        style: {
          background: "#18181b",
          color: "#f4f4f5",
          border: "1px solid #3f3f46",
        },
        success: {
          iconTheme: {
            primary: "#38bdf8",
            secondary: "#18181b",
          },
        },
        error: {
          iconTheme: {
            primary: "#f87171",
            secondary: "#18181b",
          },
        },
      }}
    />
  </StrictMode>,
);
