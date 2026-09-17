import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { installForced, installIfMissing } from "./lib/mock";

if (new URLSearchParams(window.location.search).has("mock")) {
  installForced();
} else {
  installIfMissing();
}

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
