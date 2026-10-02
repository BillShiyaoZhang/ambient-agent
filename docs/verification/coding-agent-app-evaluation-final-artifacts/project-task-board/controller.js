const { useEffect, useMemo, useState } = ambient.react;
const { Button, Card, Checkbox, Column, Row, Text, TextField } = ambient.components;

const DRAFT_KEY = "task-board.drafts.v1";

export default function App() {
  const [tasks, setTasks] = useState([]);
  const [graphError, setGraphError] = useState("");
  const [filter, setFilter] = useState("all");
  const [newTitle, setNewTitle] = useState("");
  const [renameDrafts, setRenameDrafts] = useState({});
  const [draftsReady, setDraftsReady] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    let unsubscribe;
    try {
      unsubscribe = ambient.graph.subscribe({ type: "Task" }, (result, error) => {
        if (!active) return;
        if (error) {
          setGraphError(String(error.message || error));
          return;
        }
        if (result instanceof Error) {
          setGraphError(result.message || "Could not load tasks.");
          return;
        }
        if (result && result.error) {
          setGraphError(String(result.error.message || result.error));
          return;
        }
        setGraphError("");
        setTasks(Array.isArray(result) ? result : []);
      });
    } catch (error) {
      setGraphError(error && error.message ? error.message : "Could not subscribe to tasks.");
    }
    return () => {
      active = false;
      if (typeof unsubscribe === "function") unsubscribe();
    };
  }, []);

  useEffect(() => {
    let active = true;
    ambient.storage.get(DRAFT_KEY).then((saved) => {
      if (!active) return;
      if (saved && typeof saved === "object") {
        setNewTitle(typeof saved.newTitle === "string" ? saved.newTitle : "");
        setRenameDrafts(saved.renameDrafts && typeof saved.renameDrafts === "object" ? saved.renameDrafts : {});
      }
      setDraftsReady(true);
    }).catch(() => {
      if (active) setDraftsReady(true);
    });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!draftsReady) return;
    ambient.storage.set(DRAFT_KEY, { newTitle, renameDrafts }).catch(() => {});
  }, [draftsReady, newTitle, renameDrafts]);

  const visibleTasks = useMemo(() => tasks.filter((task) => {
    if (filter === "open") return task.properties && task.properties.done !== true;
    if (filter === "completed") return task.properties && task.properties.done === true;
    return true;
  }), [tasks, filter]);

  async function runMutation(mutation) {
    setBusy(true);
    setGraphError("");
    try {
      await mutation;
    } catch (error) {
      setGraphError(error && error.message ? error.message : "The task could not be saved.");
    } finally {
      setBusy(false);
    }
  }

  function createTask() {
    const title = newTitle.trim();
    if (!title || busy) return;
    runMutation(ambient.graph.mutate([{ action: "create_node", type: "Task", properties: { title, done: false } }]));
    setNewTitle("");
  }

  function renameTask(task) {
    const title = String(renameDrafts[task.id] === undefined ? task.properties.title || "" : renameDrafts[task.id]).trim();
    if (!title || busy) return;
    runMutation(ambient.graph.mutate([{ action: "update_node_property", id: task.id, properties: { title } }]));
    const next = { ...renameDrafts };
    delete next[task.id];
    setRenameDrafts(next);
  }

  function toggleDone(task, done) {
    if (busy) return;
    runMutation(ambient.graph.mutate([{ action: "update_node_property", id: task.id, properties: { done } }]));
  }

  function deleteTask(task) {
    if (busy) return;
    runMutation(ambient.graph.mutate([{ action: "delete_node", id: task.id }]));
  }

  return ambient.html`
    <${Card} title="Project Task Board">
      <${Column} gap="12px">
        <${Row} gap="8px">
          <${TextField} value=${newTitle} placeholder="New task title" onChange=${(value) => setNewTitle(value)} onEnter=${() => createTask()} />
          <${Button} label="Add task" disabled=${busy || !newTitle.trim()} onClick=${createTask} />
        <//>
        <${Row} gap="8px">
          <${Button} label="All" disabled=${filter === "all"} onClick=${() => setFilter("all")} />
          <${Button} label="Open" disabled=${filter === "open"} onClick=${() => setFilter("open")} />
          <${Button} label="Completed" disabled=${filter === "completed"} onClick=${() => setFilter("completed")} />
        <//>
        ${graphError ? ambient.html`<${Text} text=${"Graph error: " + graphError} />` : ""}
        ${visibleTasks.length === 0 ? ambient.html`<${Text} text=${tasks.length === 0 ? "No tasks yet. Add a task to get started." : "No tasks match this filter."} />` : ""}
        ${visibleTasks.map((task) => {
          const properties = task.properties || {};
          const draft = renameDrafts[task.id] === undefined ? String(properties.title || "") : renameDrafts[task.id];
          return ambient.html`
            <${Row} gap="8px">
              <${Checkbox} checked=${properties.done === true} onChange=${(checked) => toggleDone(task, checked)} />
              <${TextField} value=${draft} onChange=${(value) => setRenameDrafts({ ...renameDrafts, [task.id]: value })} onEnter=${() => renameTask(task)} />
              <${Button} label="Rename" disabled=${busy || !String(draft).trim()} onClick=${() => renameTask(task)} />
              <${Button} label="Delete" disabled=${busy} onClick=${() => deleteTask(task)} />
            <//>
          `;
        })}
      <//>
    <//>
  `;
}
