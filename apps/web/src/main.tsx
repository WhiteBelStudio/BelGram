import React from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

function App() {
  return (
    <main className="shell">
      <section className="card">
        <div className="brand">BelGram</div>
        <h1>Мессенджер нового поколения</h1>
        <p>Фундамент BelGram 0.1.0 готов. Дальше подключаем авторизацию, чаты и realtime.</p>
        <div className="status"><span /> API foundation online</div>
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode><App /></React.StrictMode>,
);
