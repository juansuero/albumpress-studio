import "@fontsource-variable/atkinson-hyperlegible-next";
import React from "react";
import ReactDOM from "react-dom/client";
import { Toaster } from "sonner";
import App from "./App";
import { ConfirmDialogProvider } from "./ui";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfirmDialogProvider>
      <App />
      <Toaster
        position="bottom-right"
        theme="dark"
        expand={false}
        visibleToasts={2}
        closeButton
        duration={4000}
        toastOptions={{ className: "archive-toast" }}
      />
    </ConfirmDialogProvider>
  </React.StrictMode>,
);
