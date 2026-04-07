import { useEffect, useRef, useState } from "react";
import "./App.css";
import { drugs } from "./data/drugs";

type DrugProfile = {
  name: string;
  drugClass: string;
  commonUses: string;
  ai_generated?: boolean;
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
  lisinopril: { name: "Lisinopril", drugClass: "ACE Inhibitor", commonUses: "Blood pressure" },
  metoprolol: { name: "Metoprolol", drugClass: "Beta Blocker", commonUses: "Heart conditions" },
  amlodipine: { name: "Amlodipine", drugClass: "Calcium Channel Blocker", commonUses: "Blood pressure" },
  digoxin: { name: "Digoxin", drugClass: "Cardiac Glycoside", commonUses: "Heart failure" },
  furosemide: { name: "Furosemide", drugClass: "Diuretic", commonUses: "Fluid retention" },
  clopidogrel: { name: "Clopidogrel", drugClass: "Antiplatelet", commonUses: "Blood clot prevention" },
  simvastatin: { name: "Simvastatin", drugClass: "Statin", commonUses: "Cholesterol reduction" },
  ciprofloxacin: { name: "Ciprofloxacin", drugClass: "Antibiotic", commonUses: "Bacterial infections" },
  amiodarone: { name: "Amiodarone", drugClass: "Antiarrhythmic", commonUses: "Heart rhythm" },
  fluconazole: { name: "Fluconazole", drugClass: "Antifungal", commonUses: "Fungal infections" },
  clarithromycin: { name: "Clarithromycin", drugClass: "Antibiotic", commonUses: "Bacterial infections" },
  methotrexate: { name: "Methotrexate", drugClass: "DMARD", commonUses: "Arthritis, cancer" },
  prednisone: { name: "Prednisone", drugClass: "Corticosteroid", commonUses: "Inflammation" },
  lithium: { name: "Lithium", drugClass: "Mood Stabilizer", commonUses: "Bipolar disorder" },
  phenytoin: { name: "Phenytoin", drugClass: "Anticonvulsant", commonUses: "Epilepsy" },
  valproate: { name: "Valproate", drugClass: "Anticonvulsant", commonUses: "Epilepsy, bipolar" },
  tramadol: { name: "Tramadol", drugClass: "Opioid", commonUses: "Pain relief" },
  sildenafil: { name: "Sildenafil", drugClass: "PDE5 Inhibitor", commonUses: "Erectile dysfunction" },
  insulin: { name: "Insulin", drugClass: "Antidiabetic", commonUses: "Diabetes" },
  acetaminophen: { name: "Acetaminophen", drugClass: "Analgesic", commonUses: "Pain, fever" },
  naproxen: { name: "Naproxen", drugClass: "NSAID", commonUses: "Pain, inflammation" },
  hydrochlorothiazide: { name: "Hydrochlorothiazide", drugClass: "Diuretic", commonUses: "Blood pressure" },
  spironolactone: { name: "Spironolactone", drugClass: "Diuretic", commonUses: "Heart failure, BP" },
};

function renderMarkdown(text: string): string {
  return text
    .replace(/###\s(.+)/g, "<strong>$1</strong>")
    .replace(/##\s(.+)/g, "<strong>$1</strong>")
    .replace(/#\s(.+)/g, "<strong>$1</strong>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    .replace(/\n\n/g, "</p><p>")
    .replace(/\n/g, "<br/>");
}

function App() {
  const [drugA, setDrugA] = useState("");
  const [drugB, setDrugB] = useState("");
  const [suggestionsA, setSuggestionsA] = useState<string[]>([]);
  const [suggestionsB, setSuggestionsB] = useState<string[]>([]);
  const [profileA, setProfileA] = useState<DrugProfile | null>(null);
  const [profileB, setProfileB] = useState<DrugProfile | null>(null);
  const [loadingA, setLoadingA] = useState(false);
  const [loadingB, setLoadingB] = useState(false);
  const [severity, setSeverity] = useState<string | null>(null);
  const [confidence, setConfidence] = useState<number | null>(null);
  const [streamText, setStreamText] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [activeHistoryIndex, setActiveHistoryIndex] = useState<number | null>(null);
  const [sources, setSources] = useState<string[]>([]);
  const [discoveredDrugs, setDiscoveredDrugs] = useState<Record<string, DrugProfile>>({});
  const [darkMode, setDarkMode] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    const saved = localStorage.getItem("medixa-theme");
    if (saved === "dark") return true;
    if (saved === "light") return false;
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
  });

  const debounceARef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const debounceBRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

  const getFinalSearchTerm = (value: string) => {
    return value.trim().split(/\s+/).pop() ?? "";
  };

  useEffect(() => {
    document.documentElement.classList.toggle("dark", darkMode);
    localStorage.setItem("medixa-theme", darkMode ? "dark" : "light");
  }, [darkMode]);


  const fetchDrugProfile = async (name: string, setProfile: (p: DrugProfile) => void, setLoadingFn: (b: boolean) => void) => {
    const normalized = name.toLowerCase().trim();
    if (!normalized || normalized.length < 2) return;

    if (drugProfiles[normalized]) {
      setLoadingFn(false);
      setProfile({ ...drugProfiles[normalized], ai_generated: false });
      return;
    }

    if (discoveredDrugs[normalized]) {
      setLoadingFn(false);
      setProfile(discoveredDrugs[normalized]);
      return;
    }

    setLoadingFn(true);
    try {
      const resp = await fetch(`${API_URL}/drug-info?name=${encodeURIComponent(normalized)}`);
      if (resp.ok) {
        const data = await resp.json();
        if (data.is_drug === false) {
          setProfile({ name, drugClass: "Not a medication", commonUses: "Please enter a valid drug", ai_generated: false });
          return;
        }
        if (data.drugClass !== "Unknown") {
          setDiscoveredDrugs(prev => ({ ...prev, [normalized]: data }));
        }
        setProfile(data);
      }
    } catch (e) {
      console.error(e);
      setProfile({ name, drugClass: "Unknown", commonUses: "Unknown", ai_generated: false });
    } finally {
      setLoadingFn(false);
    }
  };



  const latestDrugARef = useRef("");
  const latestDrugBRef = useRef("");

  const handleDrugAChange = (value: string) => {
    setDrugA(value);
    const searchTerm = getFinalSearchTerm(value);
    latestDrugARef.current = searchTerm;
    if (!searchTerm) {
      setProfileA(null);
      setSuggestionsA([]);
      return;
    }
    const allDrugs = [...new Set([...drugs, ...Object.keys(discoveredDrugs)])];
    setSuggestionsA(allDrugs.filter((d) => d.toLowerCase().startsWith(searchTerm.toLowerCase())));
    if (debounceARef.current) clearTimeout(debounceARef.current);
    debounceARef.current = setTimeout(() => {
      if (latestDrugARef.current === searchTerm) {
        fetchDrugProfile(searchTerm, setProfileA, setLoadingA);
      }
    }, 1500);
  };

  const handleDrugBChange = (value: string) => {
    setDrugB(value);
    const searchTerm = getFinalSearchTerm(value);
    latestDrugBRef.current = searchTerm;
    if (!searchTerm) {
      setProfileB(null);
      setSuggestionsB([]);
      return;
    }
    const allDrugs = [...new Set([...drugs, ...Object.keys(discoveredDrugs)])];
    setSuggestionsB(allDrugs.filter((d) => d.toLowerCase().startsWith(searchTerm.toLowerCase())));
    if (debounceBRef.current) clearTimeout(debounceBRef.current);
    debounceBRef.current = setTimeout(() => {
      if (latestDrugBRef.current === searchTerm) {
        fetchDrugProfile(searchTerm, setProfileB, setLoadingB);
      }
    }, 1500);
  };

  const getSeverityColor = (level: string) => {
    switch (level?.toLowerCase()) {
      case "none": return "#22c55e";
      case "mild": return "#eab308";
      case "moderate": return "#f97316";
      case "severe": return "#ef4444";
      case "contraindicated": return "#18181b";
      default: return "#71717a";
    }
  };

  const getSeverityLabel = (level: string) => {
    switch (level?.toLowerCase()) {
      case "none": return "No known interaction";
      case "mild": return "Mild — monitor patient";
      case "moderate": return "Moderate — use with caution";
      case "severe": return "Severe — avoid if possible";
      case "contraindicated": return "Contraindicated — do not use together";
      default: return "Unknown";
    }
  };

  const exportHistoryPdf = () => {
    const rows = history.map((h) => `${h.timestamp} | ${h.drugA} + ${h.drugB} | ${h.severity}`);
    const popup = window.open("", "_blank");
    if (popup) {
      popup.document.write("<h3>Medixa AI Query History</h3><pre>" + rows.join("\n") + "</pre>");
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
    latestDrugARef.current = item.drugA;
    latestDrugBRef.current = item.drugB;
    if (debounceARef.current) clearTimeout(debounceARef.current);
    if (debounceBRef.current) clearTimeout(debounceBRef.current);
    fetchDrugProfile(item.drugA, setProfileA, setLoadingA);
    fetchDrugProfile(item.drugB, setProfileB, setLoadingB);
  };

  const handleAnalyse = async () => {
    setError(null);
    if (!drugA || !drugB) { setError("Please enter both drug names"); return; }
    if (drugA.toLowerCase() === drugB.toLowerCase()) { setError("Please select two different drugs"); return; }

    setStreamText("");
    setSeverity(null);
    setConfidence(null);
    setSources([]);
    setLoading(true);
    setActiveHistoryIndex(null);

    let fullText = "";
    let finalSeverity = "unknown";
    let finalConfidence: number | null = null;
    let finalSources: string[] = [];

    try {
      const response = await fetch(`${API_URL}/analyse`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ drug_a: drugA, drug_b: drugB }),
      });

      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const reader = response.body?.getReader();
      const decoder = new TextDecoder();
      if (!reader) throw new Error("No response stream");

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const lines = decoder.decode(value).split("\n");
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          let data;
          try { data = JSON.parse(line.slice(6)); } catch { continue; }

          if (data.type === "severity") {
            finalSeverity = data.data.label;
            finalConfidence = data.data.confidence;
            setSeverity(data.data.label);
            setConfidence(data.data.confidence);
          }
          if (data.type === "sources") {
            finalSources = data.data;
            setSources(data.data);
          }
          if (data.type === "token") {
            fullText += data.data;
            setStreamText(fullText);
          }
          if (data.type === "error") {
            setError(data.data);
            setLoading(false);
            return;
          }
        }
      }

      setHistory((prev) => [{
        drugA, drugB,
        severity: finalSeverity,
        confidence: finalConfidence,
        timestamp: new Date().toLocaleString(),
        explanation: fullText,
        sources: finalSources,
      }, ...prev].slice(0, 10));


    } catch (err) {
      console.error("Analysis error:", err);
      if (err instanceof Error) {
        setError(err.message.includes("HTTP") ? "Server error. Please try again." : "Failed to connect to analysis service. Please try again.");
      } else {
        setError("Failed to connect to analysis service. Please try again.");
      }
    } finally {
      setLoading(false);
    }



  };

  const DrugCard = ({ label, profile, loading: cardLoading }: { label: string; profile: DrugProfile | null; loading: boolean }) => (
    <div className="drug-card">
      <div className="drug-card-header">
        <span className="drug-card-label">{label}</span>
        {profile?.ai_generated && <span className="ai-badge">AI</span>}
      </div>
      {cardLoading ? (
        <div className="drug-card-loading">Fetching info...</div>
      ) : profile ? (
        <>
          <div className="drug-card-name">{profile.name}</div>
          <div className="drug-card-detail"><span>Class</span>{profile.drugClass}</div>
          <div className="drug-card-detail"><span>Uses</span>{profile.commonUses}</div>
        </>
      ) : (
        <div className="drug-card-empty">Enter a drug name</div>
      )}
    </div>
  );

  return (
    <div className="app">
      <header className="header">
        <div className="header-brand">
          <div className="header-logo">M</div>
          <div>
            <h1 className="header-title">Medixa AI</h1>
            <p className="header-subtitle">Understand your medications. Stay safe.</p>
          </div>
        </div>
        <button className="theme-btn" onClick={() => setDarkMode((m) => !m)}>
          {darkMode ? "Light" : "Dark"}
        </button>
      </header>

      <main className="main">
        <div className="left-panel">

          <section className="card">
            <h2 className="section-title">Drug Interaction Analysis</h2>
            <div className="input-row">
              <div className="input-wrap">
                <label className="input-label">Drug A</label>
                <input
                  className="input"
                  value={drugA}
                  onChange={(e) => handleDrugAChange(e.target.value)}
                  placeholder="e.g. warfarin"
                  disabled={loading}
                />
                {suggestionsA.length > 0 && (
                  <ul className="suggestions">
                    {suggestionsA.map((s) => (
                      <li key={s} onClick={() => {
                        setDrugA(s);
                        setSuggestionsA([]);
                        latestDrugARef.current = s;
                        if (debounceARef.current) clearTimeout(debounceARef.current);
                        setProfileA(null);
                        fetchDrugProfile(s, setProfileA, setLoadingA);
                      }}>{s}</li>                    ))}
                  </ul>
                )}
              </div>

              <div className="vs-divider">VS</div>

              <div className="input-wrap">
                <label className="input-label">Drug B</label>
                <input
                  className="input"
                  value={drugB}
                  onChange={(e) => handleDrugBChange(e.target.value)}
                  placeholder="e.g. aspirin"
                  disabled={loading}
                />
                {suggestionsB.length > 0 && (
                  <ul className="suggestions">
                    {suggestionsB.map((s) => (
                      <li key={s} onClick={() => {
                        setDrugB(s);
                        setSuggestionsB([]);
                        latestDrugBRef.current = s;
                        if (debounceBRef.current) clearTimeout(debounceBRef.current);
                        setProfileB(null);
                        fetchDrugProfile(s, setProfileB, setLoadingB);
                      }}>{s}</li>                    ))}
                  </ul>
                )}
              </div>
            </div>

            <div className="drug-cards-row">
              <DrugCard label="Drug A" profile={profileA} loading={loadingA} />
              <DrugCard label="Drug B" profile={profileB} loading={loadingB} />
            </div>

            <button className="analyse-btn" onClick={handleAnalyse} disabled={loading}>
              {loading ? <><span className="spinner" />Analysing...</> : "Analyse Interaction"}
            </button>
            {error && <p className="error-text">{error}</p>}
          </section>

          {severity && (
            <section className="card result-card">
              <div className="severity-row">
                <span className="severity-badge" style={{ backgroundColor: getSeverityColor(severity) }}>
                  {severity.toUpperCase()}
                </span>
                <span className="severity-desc">{getSeverityLabel(severity)}</span>
              </div>

              {confidence !== null && (
                <div className="confidence-row">
                  <span className="confidence-label">Model confidence</span>
                  <div className="meter-track">
                    <div className="meter-fill" style={{ width: `${confidence * 100}%`, backgroundColor: getSeverityColor(severity) }} />
                  </div>
                  <span className="confidence-value">{(confidence * 100).toFixed(0)}%</span>
                </div>
              )}

              {sources.length > 0 && (
                <div className="sources-row">
                  <span className="sources-label">Sources</span>
                  {sources.map((s, i) => <span key={i} className="source-chip">{s}</span>)}
                </div>
              )}
            </section>
          )}

          <section className="card explanation-card">
            <div className="explanation-header">
              <h2 className="section-title">Clinical Explanation</h2>
              {loading && <span className="cursor-blink">▌</span>}
            </div>
            {!streamText && !loading && (
              <p className="muted-text">Enter two drug names above and click Analyse to see the interaction analysis.</p>
            )}
            {!streamText && loading && (
              <p className="muted-text">Generating clinical explanation...</p>
            )}
            {streamText && (
              <div
                className="explanation-text"
                dangerouslySetInnerHTML={{ __html: "<p>" + renderMarkdown(streamText) + "</p>" }}
              />
            )}
          </section>

          <section className="card dashboard-card">
            <h2 className="section-title">System</h2>
            <div className="dashboard-links">
              <a href="http://localhost:3000" target="_blank" rel="noreferrer" className="dash-link">Grafana</a>
              <a href="http://localhost:9090" target="_blank" rel="noreferrer" className="dash-link">Prometheus</a>
              <a href="http://localhost:8000/docs" target="_blank" rel="noreferrer" className="dash-link">API Docs</a>
              <a href="http://localhost:8001/docs" target="_blank" rel="noreferrer" className="dash-link">ML Docs</a>
            </div>
          </section>
        </div>

        <aside className="right-panel card">
          <div className="history-header">
            <h2 className="section-title">Query History</h2>
            <button className="export-btn" onClick={exportHistoryPdf} disabled={history.length === 0}>Export PDF</button>
          </div>

          {history.length === 0 ? (
            <p className="muted-text">No queries yet.</p>
          ) : (
            <div className="history-list">
              {history.map((item, index) => (
                <div
                  key={index}
                  className={`history-item ${activeHistoryIndex === index ? "active" : ""}`}
                  onClick={() => replayHistory(item, index)}
                >
                  <div className="history-drugs">
                    <strong>{item.drugA}</strong> + <strong>{item.drugB}</strong>
                  </div>
                  <div className="history-meta">
                    <span className="history-badge" style={{ backgroundColor: getSeverityColor(item.severity) }}>
                      {item.severity}
                    </span>
                    <span className="history-time">{item.timestamp}</span>
                  </div>
                  {activeHistoryIndex === index && (
                    <div className="history-explanation">{item.explanation}</div>
                  )}
                </div>
              ))}
            </div>
          )}
        </aside>
      </main>
    </div>
  );
}

export default App;