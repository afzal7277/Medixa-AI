import { useEffect, useState } from "react";
import "./App.css";
import { drugs } from "./data/drugs";

type DrugProfile = {
  name: string;
  drugClass: string;
  commonUses: string;
};

type HistoryItem = {
  drugA: string;
  drugB: string;
  severity: string;
  confidence: number | null;
  timestamp: string;
  explanation: string;
  sources: string[];
};

const drugProfiles: Record<string, DrugProfile> = {
  aspirin: { name: "Aspirin", drugClass: "NSAID", commonUses: "Pain, fever, inflammation" },
  warfarin: { name: "Warfarin", drugClass: "Anticoagulant", commonUses: "Blood clot prevention" },
  paracetamol: { name: "Paracetamol", drugClass: "Analgesic", commonUses: "Pain, fever" },
  ibuprofen: { name: "Ibuprofen", drugClass: "NSAID", commonUses: "Pain, inflammation" },
  metformin: { name: "Metformin", drugClass: "Antidiabetic", commonUses: "Type 2 diabetes" },
  atorvastatin: { name: "Atorvastatin", drugClass: "Statin", commonUses: "Cholesterol reduction" },
  amoxicillin: { name: "Amoxicillin", drugClass: "Antibiotic", commonUses: "Bacterial infections" },
  omeprazole: { name: "Omeprazole", drugClass: "PPI", commonUses: "Acid reflux" },
};

function App() {
  const [drugA, setDrugA] = useState("");
  const [drugB, setDrugB] = useState("");

  const [suggestionsA, setSuggestionsA] = useState<string[]>([]);
  const [suggestionsB, setSuggestionsB] = useState<string[]>([]);

  const [severity, setSeverity] = useState<string | null>(null);
  const [confidence, setConfidence] = useState<number | null>(null);
  const [streamText, setStreamText] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [activeHistoryIndex, setActiveHistoryIndex] = useState<number | null>(null);
  const [sources, setSources] = useState<string[]>(["OpenFDA (placeholder)"]);
  const [metricData, setMetricData] = useState({ apiRate: 0, mlLatency: 0, streamingLatency: 0, pipelineLag: 0, kafkaHealthy: true });

  const [darkMode, setDarkMode] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    const saved = localStorage.getItem("medixa-theme");
    if (saved === "dark") return true;
    if (saved === "light") return false;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  });

  useEffect(() => {
    document.documentElement.classList.toggle("dark", darkMode);
    localStorage.setItem("medixa-theme", darkMode ? "dark" : "light");
  }, [darkMode]);

  useEffect(() => {
    const interval = setInterval(() => {
      setMetricData({
        apiRate: Math.round(10 + Math.random() * 35),
        mlLatency: Math.round(120 + Math.random() * 230),
        streamingLatency: Math.round(70 + Math.random() * 95),
        pipelineLag: Math.round(Math.random() * 2),
        kafkaHealthy: Math.random() > 0.08,
      });
    }, 2200);
    return () => clearInterval(interval);
  }, []);

  const handleSuggest = (value: string, setFn: (newValues: string[]) => void) => {
  if (!value) {
    setFn([]);
    return;
  }

  const filtered = drugs
    .filter((d) => d.toLowerCase().includes(value.toLowerCase()))
    .slice(0, 5);

  setFn(filtered);
};

const getDrugProfile = (name: string): DrugProfile => {
  const normalized = name.toLowerCase();
  return drugProfiles[normalized] || { name, drugClass: "Unknown", commonUses: "Unknown" };
};

const formatLegend = (level: string) => {
  switch (level) {
    case "none":
      return "No interaction";
    case "mild":
      return "Mild interaction";
    case "moderate":
      return "Moderate interaction";
    case "severe":
      return "Severe interaction";
    case "contraindicated":
      return "Contraindicated";
    default:
      return "Unknown";
  }
};

const getSeverityColor = (level: string) => {
  switch (level) {
    case "none":
      return "#4caf50";
    case "mild":
      return "#ffeb3b";
    case "moderate":
      return "#ff9800";
    case "severe":
      return "#f44336";
    case "contraindicated":
      return "#000";
    default:
      return "#ccc";
  }
};

const exportHistoryPdf = () => {
  const rows = history.map((h) => `${h.timestamp} | ${h.drugA} + ${h.drugB} | ${h.severity}`);
  const popup = window.open("", "_blank");
  if (popup) {
    popup.document.write("<h3>Medixa AI Query History</h3>");
    popup.document.write("<pre>" + rows.join("\n") + "</pre>");
    popup.document.close();
    popup.print();
  }
};

const replayHistory = (item: HistoryItem, index: number) => {
  setActiveHistoryIndex(index);
  setDrugA(item.drugA);
  setDrugB(item.drugB);
  setSeverity(item.severity);
  setConfidence(item.confidence);
  setStreamText(item.explanation);
  setSources(item.sources);
};

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

const handleAnalyse = async () => {
  setError(null);

  if (!drugA || !drugB) {
    setError("Please enter both drugs");
    return;
  }

  if (drugA === drugB) {
    setError("Select two different drugs");
    return;
  }

  setStreamText("");
  setSeverity(null);
  setConfidence(null);
  setLoading(true);

  let fullText = "";
  let finalSeverity = "unknown";
  let finalConfidence: number | null = null;
  let finalSources: string[] = [];

  try {
    const response = await fetch(`${API_URL}/analyse`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ drug_a: drugA, drug_b: drugB }),
    });

    const reader = response.body?.getReader();
    const decoder = new TextDecoder();

    if (!reader) throw new Error("No response");

    const startTime = Date.now();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const chunk = decoder.decode(value);
      const lines = chunk.split("\n");

      lines.forEach((line) => {
        if (!line) return;
        if (line.startsWith("data: ")) {
          const payload = line.replace("data: ", "");
          let data;
          try {
            data = JSON.parse(payload);
          } catch {
            return;
          }

          if (data.type === "severity") {
            setSeverity(data.data.label);
            setConfidence(data.data.confidence);
            finalSeverity = data.data.label;
            finalConfidence = data.data.confidence;
            finalSources = data.data.sources ?? finalSources;
          }

          if (data.type === "token") {
            fullText += data.data;
            setStreamText(fullText);
          }
        }
      });
    }

    setSources(finalSources.length > 0 ? finalSources : ["OpenFDA (placeholder)"]);

    setHistory((prev) => [
      {
        drugA,
        drugB,
        severity: finalSeverity,
        confidence: finalConfidence,
        timestamp: new Date().toLocaleString(),
        explanation: fullText,
        sources: finalSources.length > 0 ? finalSources : ["OpenFDA (placeholder)"],
      },
      ...prev,
    ].slice(0, 10));

    if (finalConfidence !== null) {
      setConfidence(finalConfidence);
    }

    if (Date.now() - startTime > 10000) {
      // just a guard; metrics are simulated.
    }
  } catch (err) {
    console.error(err);
    setError("Failed to analyse");
  } finally {
    setLoading(false);
  }
};

return (
  <div className="container">
    <header className="app-header">
      <div>
        <h1>Medixa AI</h1>
        <p>Understand your medications. Stay safe.</p>
      </div>
      <div className="metrics-level">
        <span>API Rate: {metricData.apiRate}/s</span>
        <span>ML Latency: {metricData.mlLatency}ms</span>
        <span>LLM latency: {metricData.streamingLatency}ms</span>
        <span>Pipeline lag: {metricData.pipelineLag}s</span>
      </div>
      <button className="theme-toggle" onClick={() => setDarkMode((m) => !m)}>
        {darkMode ? "☀️ Light" : "🌙 Dark"}
      </button>
    </header>

    <div className="row">
      <div className="main-panel">
        <div className="card control-card">
          <div className="input-group">
            <div className="input-wrapper">
              <label>Drug A</label>
              <input
                value={drugA}
                onChange={(e) => {
                  setDrugA(e.target.value);
                  handleSuggest(e.target.value, setSuggestionsA);
                }}
                placeholder="Drug A"
              />
              {suggestionsA.length > 0 && (
                <ul className="suggestions">
                  {suggestionsA.map((s) => (
                    <li
                      key={s}
                      onClick={() => {
                        setDrugA(s);
                        setSuggestionsA([]);
                      }}
                    >
                      {s}
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className="input-wrapper">
              <label>Drug B</label>
              <input
                value={drugB}
                onChange={(e) => {
                  setDrugB(e.target.value);
                  handleSuggest(e.target.value, setSuggestionsB);
                }}
                placeholder="Drug B"
              />
              {suggestionsB.length > 0 && (
                <ul className="suggestions">
                  {suggestionsB.map((s) => (
                    <li
                      key={s}
                      onClick={() => {
                        setDrugB(s);
                        setSuggestionsB([]);
                      }}
                    >
                      {s}
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <button onClick={handleAnalyse} disabled={loading}>
              {loading ? "Analysing..." : "Analyse"}
            </button>
          </div>
          {error && <p className="error-text">{error}</p>}

          <div className="card small-cards">
            <article className="micro-card">
              <h4>Drug Card A</h4>
              <p>{getDrugProfile(drugA).name}</p>
              <p>Class: {getDrugProfile(drugA).drugClass}</p>
              <p>Use: {getDrugProfile(drugA).commonUses}</p>
            </article>
            <article className="micro-card">
              <h4>Drug Card B</h4>
              <p>{getDrugProfile(drugB).name}</p>
              <p>Class: {getDrugProfile(drugB).drugClass}</p>
              <p>Use: {getDrugProfile(drugB).commonUses}</p>
            </article>
          </div>

          <div className="severity-panel">
            <span className="severity-label" style={{ backgroundColor: getSeverityColor(severity ?? "") }}>
              {severity ? severity.toUpperCase() : "NO RESULT"}
            </span>
            <span className="mt-lvl">{formatLegend(severity ?? "unknown")}</span>
          </div>

          <div className="confidence-meter">
            <div className="meter-track">
              <div className="meter-fill" style={{ width: `${(confidence ?? 0) * 100}%` }} />
            </div>
            <span>{confidence !== null ? `${(confidence * 100).toFixed(0)}%` : "No confidence"}</span>
          </div>

          <div className="sources-panel">
            <h4>Sources</h4>
            <ul>
              {sources.map((item, idx) => (
                <li key={`src-${idx}`}>{item}</li>
              ))}
            </ul>
          </div>
        </div>

        <div className="card explanation-card">
          <h3>Streaming Explanation {loading && <span className="cursor">▌</span>}</h3>
          <p>{streamText || "No explanation yet"}</p>
        </div>

        <div className="card dashboard-card">
          <h3>System Dashboard</h3>
          <div className="dash-grid">
            <div className="dash-box">API requests: {metricData.apiRate}/s</div>
            <div className="dash-box">ML latency: {metricData.mlLatency} ms</div>
            <div className="dash-box">LLM lag: {metricData.streamingLatency} ms</div>
            <div className="dash-box">Pipeline lag: {metricData.pipelineLag} s</div>
            <div className="dash-box">Kafka Consumer: {metricData.kafkaHealthy ? "OK" : "DELAY"}</div>
          </div>
        </div>
      </div>

      <aside className="history-panel card">
        <h3>Query History</h3>
        <button onClick={exportHistoryPdf}>Export as PDF</button>
        {history.length === 0 ? (
          <p className="muted">No history yet.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Drug A</th>
                <th>Drug B</th>
                <th>Severity</th>
                <th>Timestamp</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {history.flatMap((item, index) => {
                const mainRow = (
                  <tr
                    key={`history-${index}`}
                    className={activeHistoryIndex === index ? "active" : ""}
                    onClick={() => replayHistory(item, index)}
                  >
                    <td>{item.drugA}</td>
                    <td>{item.drugB}</td>
                    <td>
                      <span className="badge" style={{ backgroundColor: getSeverityColor(item.severity) }}>
                        {item.severity}
                      </span>
                    </td>
                    <td>{item.timestamp}</td>
                    <td>View</td>
                  </tr>
                );
                const expandedRow =
                  activeHistoryIndex === index ? (
                    <tr key={`history-exp-${index}`} className="history-extra">
                      <td colSpan={5}>
                        <strong>Explanation:</strong> {item.explanation}
                      </td>
                    </tr>
                  ) : null;
                return [mainRow, expandedRow].filter(Boolean);
              })}
            </tbody>
          </table>
        )}
      </aside>
    </div>
  </div>
);
}

export default App;
