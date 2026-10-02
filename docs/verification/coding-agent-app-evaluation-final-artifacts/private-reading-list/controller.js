const { useEffect, useState } = ambient.react;
const { Card, Column, Row, Text, Button, TextField, Checkbox } = ambient.components;

export default function App() {
  const [books, setBooks] = useState([]);
  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState("");
  const [editingId, setEditingId] = useState(null);
  const [status, setStatus] = useState("Loading your reading list…");
  const [problem, setProblem] = useState("");
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [draftsHydrated, setDraftsHydrated] = useState(false);

  useEffect(() => {
    let active = true;
    setStatus("Loading your reading list…");
    ambient.files.read("books.json").then((raw) => {
      if (!active) return;
      let parsed = [];
      if (raw != null && raw !== "") {
        const value = typeof raw === "string" ? JSON.parse(raw) : raw;
        parsed = Array.isArray(value) ? value : [];
      }
      setBooks(parsed.filter((book) => book && typeof book.id === "string"));
      setLoaded(true);
      setLoadFailed(false);
      setStatus("");
      setProblem("");
    }).catch((error) => {
      if (!active) return;
      setStatus("");
      const missing = error && (error.code === "file_not_found" || error.message === "App data file not found");
      if (missing) {
        setBooks([]);
        setLoaded(true);
        setLoadFailed(false);
        setProblem("");
      } else {
        setLoadFailed(true);
        setProblem("We couldn’t load your books. Please retry before making changes.");
      }
    });
    return () => { active = false; };
  }, [loadAttempt]);

  useEffect(() => {
    let active = true;
    Promise.all([ambient.storage.get("draft-title"), ambient.storage.get("draft-author"), ambient.storage.get("draft-editing-id")]).then((values) => {
      if (!active) return;
      if (typeof values[0] === "string") setTitle(values[0]);
      if (typeof values[1] === "string") setAuthor(values[1]);
      if (typeof values[2] === "string") setEditingId(values[2]);
      setDraftsHydrated(true);
    }).catch(() => {});
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!draftsHydrated) return;
    ambient.storage.set("draft-title", title).catch(() => {});
  }, [title, draftsHydrated]);

  useEffect(() => {
    if (!draftsHydrated) return;
    ambient.storage.set("draft-author", author).catch(() => {});
  }, [author, draftsHydrated]);

  useEffect(() => {
    if (!draftsHydrated) return;
    if (editingId) ambient.storage.set("draft-editing-id", editingId).catch(() => {});
    else ambient.storage.delete("draft-editing-id").catch(() => {});
  }, [editingId, draftsHydrated]);

  function save(next) {
    if (!loaded) return;
    setBooks(next);
    setProblem("");
    setStatus("Saving…");
    ambient.files.write("books.json", JSON.stringify(next)).then(() => {
      setStatus("Saved privately");
      setProblem("");
    }).catch(() => {
      setStatus("");
      setProblem("We couldn’t save your changes. Please try again.");
    });
  }

  function submit() {
    if (!loaded) {
      setProblem("Your list is still loading. Retry loading before making changes.");
      return;
    }
    const cleanTitle = title.trim();
    const cleanAuthor = author.trim();
    if (!cleanTitle || !cleanAuthor) {
      setProblem("Enter both a title and an author.");
      return;
    }
    let next;
    if (editingId) {
      next = books.map((book) => book.id === editingId ? { ...book, title: cleanTitle, author: cleanAuthor } : book);
    } else {
      next = [...books, { id: String(Date.now()) + Math.random().toString(36).slice(2), title: cleanTitle, author: cleanAuthor, read: false }];
    }
    save(next);
    setTitle("");
    setAuthor("");
    setEditingId(null);
  }

  function beginEdit(book) {
    setEditingId(book.id);
    setTitle(book.title);
    setAuthor(book.author);
    setProblem("");
  }

  function cancelEdit() {
    setEditingId(null);
    setTitle("");
    setAuthor("");
    setProblem("");
  }

  return ambient.html`
    <${Card} title="Private Reading List">
      <${Column} gap="12px">
        <${Text} text="Keep track of the books you want to read." />
        <${Column} gap="8px">
          <${TextField} label="Book title" value=${title} placeholder="Title" onChange=${setTitle} onEnter=${submit} />
          <${TextField} label="Author" value=${author} placeholder="Author" onChange=${setAuthor} onEnter=${submit} />
          <${Row} gap="8px">
            <${Button} label=${editingId ? "Save changes" : "Add book"} onClick=${submit} />
            ${editingId ? ambient.html`<${Button} label="Cancel" onClick=${cancelEdit} />` : null}
          <//>
        <//>
        ${status ? ambient.html`<${Text} text=${status} />` : null}
        ${problem ? ambient.html`
          <${Column} gap="8px">
            <${Text} text=${problem} />
            ${loadFailed ? ambient.html`<${Button} label="Retry loading" onClick=${() => setLoadAttempt((attempt) => attempt + 1)} />` : null}
          <//>
        ` : null}
        ${books.length === 0 && !status && !problem ? ambient.html`<${Text} text="Your list is empty. Add a book to get started." />` : null}
        ${books.length > 0 ? ambient.html`
          <${Column} gap="8px">
            ${books.map((book) => ambient.html`
              <${Row} key=${book.id} gap="8px">
                <${Column} gap="2px">
                  <${Text} text=${book.title} />
                  <${Text} text=${"by " + book.author} />
                <//>
                <${Checkbox} label="Read" checked=${Boolean(book.read)} onChange=${(checked) => save(books.map((item) => item.id === book.id ? { ...item, read: checked } : item))} />
                <${Button} label="Edit" onClick=${() => beginEdit(book)} />
                <${Button} label="Delete" onClick=${() => save(books.filter((item) => item.id !== book.id))} />
              <//>
            `)}
          <//>
        ` : null}
      <//>
    <//>
  `;
}
