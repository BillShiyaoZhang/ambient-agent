const { useEffect, useState } = ambient.react;
const { Card, Column, Row, Text, Button, TextField, Checkbox, List } = ambient.components;

export default function App() {
  const [books, setBooks] = useState([]);
  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState("");
  const [editingId, setEditingId] = useState(null);
  const [status, setStatus] = useState("Loading your reading list…");
  const [problem, setProblem] = useState("");

  useEffect(() => {
    let active = true;
    ambient.files.read("books.json").then((raw) => {
      if (!active) return;
      let parsed = [];
      if (raw != null && raw !== "") {
        const value = typeof raw === "string" ? JSON.parse(raw) : raw;
        parsed = Array.isArray(value) ? value : [];
      }
      setBooks(parsed.filter((book) => book && typeof book.id === "string"));
      setStatus("");
      setProblem("");
    }).catch(() => {
      if (!active) return;
      setBooks([]);
      setStatus("");
      setProblem("We couldn’t load your books. You can retry, or add a book and save the list.");
    });
    return () => { active = false; };
  }, []);

  function save(next) {
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
        ${problem ? ambient.html`<${Text} text=${problem} />` : null}
        ${books.length === 0 && !status ? ambient.html`<${Text} text="Your list is empty. Add a book to get started." />` : null}
        ${books.length > 0 ? ambient.html`
          <${List}>
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
