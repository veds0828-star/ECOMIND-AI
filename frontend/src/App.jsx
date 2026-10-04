import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";

function App() {
  const [message, setMessage] = useState("");
  const [chat, setChat] = useState([]);

  useEffect(() => {
    fetch("http://127.0.0.1:8000/history")
      .then((res) => res.json())
      .then((data) => {
        const formatted = data.flatMap((item) => [
          { role: "user", text: item.question },
          { role: "ai", text: item.answer },
        ]);
        setChat(formatted);
      });
  }, []);

  const sendMessage = async () => {
    if (!message) return;

    const userMessage = message;

    setChat((prev) => [...prev, { role: "user", text: userMessage }]);
    setMessage("");

    const res = await fetch("http://127.0.0.1:8000/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: userMessage }),
    });

    const data = await res.json();

    setChat((prev) => [
      ...prev,
      {
        role: "ai",
        text: data.response,
        question: userMessage,
        facets: data.facets || [],
        expanded: {},
        privacy: data.privacy || null,
      },
    ]);
  };

  const handleFacetClick = async (msgIndex, facet) => {
    const targetMsg = chat[msgIndex];

    const res = await fetch("http://127.0.0.1:8000/expand", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: targetMsg.question, facet }),
    });

    const data = await res.json();

    setChat((prev) => {
      const updated = [...prev];
      updated[msgIndex] = {
        ...updated[msgIndex],
        expanded: {
          ...updated[msgIndex].expanded,
          [facet]: data.content,
        },
      };
      return updated;
    });
  };

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100 flex flex-col">

      {/* Header */}
      <header className="border-b border-emerald-900/40 bg-neutral-950/80 backdrop-blur sticky top-0 z-10">
        <div className="max-w-4xl mx-auto px-6 py-4 flex items-center justify-between">

          <div>
            <h1 className="text-2xl font-bold text-emerald-400 tracking-tight">
              EcoMind <span className="text-neutral-100">AI</span>
            </h1>
            <p className="text-xs text-neutral-500 italic mt-0.5">
              "Smarter AI. Lighter footprint."
            </p>
          </div>

          <div className="flex items-center gap-3">
            <div className="hidden sm:flex items-center gap-1.5 bg-emerald-950/60 border border-emerald-800/50 text-emerald-400 text-sm px-3 py-1.5 rounded-full">
              <span>🌱</span>
              <span className="font-medium">120 Eco Credits</span>
            </div>

            <button className="text-sm text-neutral-300 hover:text-emerald-400 transition-colors px-3 py-1.5">
              Log in
            </button>
            <button className="text-sm bg-emerald-600 hover:bg-emerald-500 text-neutral-950 font-medium px-4 py-1.5 rounded-lg transition-colors">
              Sign up
            </button>
          </div>

        </div>
      </header>

      {/* Chat area (full length) */}
      <main className="flex-1 max-w-4xl w-full mx-auto px-6 py-8 flex flex-col">

        <div className="flex-1 flex flex-col gap-4">
          {chat.length === 0 && (
            <div className="flex-1 flex flex-col items-center justify-center text-center text-neutral-600 gap-2">
              <span className="text-4xl">🌍</span>
              <p className="text-sm">Ask EcoMind anything — efficiently.</p>
            </div>
          )}

          {chat.map((msg, index) => (
            <div
              key={index}
              className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
            >
              <div
                className={`max-w-[80%] px-4 py-3 rounded-2xl text-sm leading-relaxed ${
                  msg.role === "user"
                    ? "bg-emerald-600 text-neutral-950 font-medium rounded-br-sm"
                    : "bg-neutral-900 border border-neutral-800 text-neutral-200 rounded-bl-sm"
                }`}
              >
                <ReactMarkdown>{msg.text}</ReactMarkdown>

                {msg.role === "ai" && msg.privacy && msg.privacy.has_sensitive_data && (
                  <div
                    className={`mt-2 mb-1 text-xs px-3 py-2 rounded-lg border flex items-center gap-2 ${
                      msg.privacy.risk_level === "critical"
                        ? "bg-red-950/40 border-red-800/50 text-red-400"
                        : msg.privacy.risk_level === "high"
                        ? "bg-orange-950/40 border-orange-800/50 text-orange-400"
                        : "bg-yellow-950/40 border-yellow-800/50 text-yellow-400"
                    }`}
                  >
                    <span>🔒</span>
                    <span>
                      Privacy protected — detected: {msg.privacy.detected_types.join(", ")}
                      {" "}(risk: {msg.privacy.risk_level}). Sensitive data was masked before
                      sending to the AI.
                    </span>
                  </div>
                )}

                {msg.role === "ai" && msg.facets && msg.facets.length > 0 && (
                  <div className="flex flex-wrap gap-2 mt-3">
                    {msg.facets.map((facet) => (
                      <button
                        key={facet}
                        onClick={() => handleFacetClick(index, facet)}
                        disabled={msg.expanded && msg.expanded[facet] !== undefined}
                        className="text-xs bg-emerald-950/60 border border-emerald-800/50 text-emerald-400 px-3 py-1.5 rounded-full hover:bg-emerald-900/60 transition-colors disabled:opacity-40 disabled:cursor-default"
                      >
                        {facet}
                      </button>
                    ))}
                  </div>
                )}

                {msg.expanded &&
                  Object.entries(msg.expanded).map(([facet, content]) => (
                    <div
                      key={facet}
                      className="mt-3 pt-3 border-t border-neutral-800 text-neutral-300"
                    >
                      <p className="text-xs font-semibold text-emerald-400 mb-1">{facet}</p>
                      <ReactMarkdown>{content}</ReactMarkdown>
                    </div>
                  ))}
              </div>
            </div>
          ))}
        </div>

        {/* Input bar */}
        <div className="sticky bottom-6 mt-6">
          <div className="flex items-center gap-2 bg-neutral-900/60 backdrop-blur-md border border-emerald-800/40 rounded-2xl px-4 py-3 shadow-lg shadow-emerald-950/30">
            <input
              className="flex-1 bg-transparent outline-none text-neutral-100 placeholder-neutral-500 text-sm"
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && sendMessage()}
              placeholder="Ask EcoMind..."
            />
            <button
              className="bg-emerald-600 hover:bg-emerald-500 text-neutral-950 font-medium text-sm px-4 py-2 rounded-xl transition-colors"
              onClick={sendMessage}
            >
              Send
            </button>
          </div>
        </div>

      </main>

    </div>
  );
}

export default App;