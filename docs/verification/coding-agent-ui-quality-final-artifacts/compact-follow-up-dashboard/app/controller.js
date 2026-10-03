const { useEffect, useState, useRef } = ambient.react;
const { Card, Column, Row, Text, Button, TextField, Checkbox } = ambient.components;

const FILE_PATH = "follow-ups.json";

function isMissingFile(error) {
  return Boolean(error && (error.code === "file_not_found" || error.message === "App data file not found"));
}

function parseEntries(text) {
  const value = JSON.parse(text);
  if (!Array.isArray(value)) throw new Error("Invalid follow-up data");
  return value.filter((item) => item && typeof item.title === "string" && typeof item.done === "boolean").map((item) => ({ title: item.title, done: item.done }));
}

export default function App() {
  const [items, setItems] = useState([]);
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState(-1);
  const [editTitle, setEditTitle] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);
  const latest = useRef([]);
  const pendingWrite = useRef(null);

  useEffect(() => {
    let active = true;
    ambient.files.read("follow-ups.json").then((text) => {
      if (!active) return;
      const parsed = parseEntries(text);
      latest.current = parsed;
      setItems(parsed);
      setLoading(false);
      setReady(true);
    }).catch((err) => {
      if (!active) return;
      if (isMissingFile(err)) {
        latest.current = [];
        setItems([]);
        setLoading(false);
        setReady(true);
      } else {
        setError("Could not load follow-ups. Try again.");
        setLoading(false);
      }
    });
    return () => { active = false; };
  }, []);

  function save(next) {
    latest.current = next;
    setItems(next);
    setError("");
    if (ready) {
      const snapshot = JSON.stringify(next);
      pendingWrite.current = snapshot;
      ambient.files.write("follow-ups.json", snapshot).then(() => {
        if (pendingWrite.current === snapshot) pendingWrite.current = null;
      }).catch(() => setError("Could not save changes. Try again."));
    }
  }

  function addItem() {
    const title = draft.trim();
    if (!title) return;
    save([...items, { title, done: false }]);
    setDraft("");
  }

  function commitRename(index) {
    const title = editTitle.trim();
    if (title) save(items.map((item, i) => i === index ? { ...item, title } : item));
    setEditing(-1);
    setEditTitle("");
  }

  function retry() {
    if (pendingWrite.current !== null) {
      const snapshot = pendingWrite.current;
      setError("");
      ambient.files.write("follow-ups.json", snapshot).then(() => {
        if (pendingWrite.current === snapshot) pendingWrite.current = null;
      }).catch(() => setError("Could not save changes. Try again."));
      return;
    }
    setLoading(true);
    setError("");
    ambient.files.read("follow-ups.json").then((text) => {
      const parsed = parseEntries(text);
      latest.current = parsed;
      setItems(parsed);
      setReady(true);
      setLoading(false);
    }).catch((err) => {
      if (isMissingFile(err)) {
        setItems([]);
        latest.current = [];
        setReady(true);
        setLoading(false);
      } else {
        setError("Could not load follow-ups. Try again.");
        setLoading(false);
      }
    });
  }

  const pending = items.filter((item) => !item.done).length;

  return ambient.html`
    <${Column} gap="12px" style=${{ padding: "16px", maxWidth: "680px", margin: "0 auto", color: "var(--ambient-text, inherit)" }}>
      <${Card}>
        <${Column} gap="10px">
          <${Row} style=${{ alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: "8px" }}>
            <${Text} text="Follow-ups" style=${{ fontSize: "20px", fontWeight: 700 }} />
            <${Text} text=${loading ? "Loading…" : `${pending} open · ${items.length} total`} />
          <//>
          <${Row} gap="8px" style=${{ alignItems: "center" }}>
            <${TextField} value=${draft} placeholder="Add a follow-up" aria-label="New follow-up title" onChange=${setDraft} onEnter=${addItem} />
            <${Button} onClick=${addItem} disabled=${loading || !draft.trim()} aria-label="Add follow-up">Add</${Button}>
          <//>
        <//>
      <//>
      ${error ? ambient.html`<${Card}><${Row} style=${{ alignItems: "center", justifyContent: "space-between", gap: "8px" }}><${Text} text=${error} /><${Button} onClick=${retry}>Retry</${Button}><//></${Card}>` : null}
      ${loading ? ambient.html`<${Text} text="Loading follow-ups…" />` : items.length === 0 ? ambient.html`<${Card}><${Text} text="No follow-ups yet. Add one above to get started." /></${Card}>` : ambient.html`
        <${Column} gap="8px">
          ${items.map((item, index) => ambient.html`
            <${Card} key=${index}>
              <${Row} gap="10px" style=${{ alignItems: "center", flexWrap: "wrap" }}>
                <${Checkbox} checked=${item.done} aria-label=${item.done ? `Reopen ${item.title}` : `Complete ${item.title}`} onChange=${(checked) => save(items.map((entry, i) => i === index ? { ...entry, done: checked } : entry))} />
                ${editing === index ? ambient.html`
                  <${TextField} value=${editTitle} aria-label="Rename follow-up" onChange=${setEditTitle} onEnter=${() => commitRename(index)} />
                  <${Button} onClick=${() => commitRename(index)}>Save</${Button}>
                  <${Button} onClick=${() => setEditing(-1)}>Cancel</${Button}>
                ` : ambient.html`
                  <${Text} text=${item.title} style=${{ flex: "1 1 160px", textDecoration: item.done ? "line-through" : "none", opacity: item.done ? 0.65 : 1, overflowWrap: "anywhere" }} />
                  <${Button} onClick=${() => { setEditing(index); setEditTitle(item.title); }}>Rename</${Button}>
                  <${Button} onClick=${() => save(items.filter((_, i) => i !== index))} aria-label=${`Remove ${item.title}`}>Remove</${Button}>
                `}
              <//>
            <//>
          `)}
        <//>
      `}
    <//>
  `;
}
