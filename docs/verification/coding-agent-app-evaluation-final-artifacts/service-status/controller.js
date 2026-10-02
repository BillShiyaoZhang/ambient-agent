const { useEffect, useState, useCallback } = ambient.react;
const { Card, Column, Row, Text, Button } = ambient.components;

function readServices(payload) {
  if (!payload || typeof payload !== "object") {
    throw new Error("The status response was not valid JSON data.");
  }

  const entries = Array.isArray(payload) ? payload : payload.services;
  if (!Array.isArray(entries)) {
    throw new Error("The status response did not contain a services list.");
  }

  const services = [];
  for (const entry of entries) {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
      throw new Error("The status response contained an invalid service.");
    }
    if (typeof entry.name !== "string" || typeof entry.status !== "string") {
      throw new Error("A service was missing its name or status.");
    }
    const name = entry.name.trim();
    const status = entry.status.trim();
    if (!name || !status) {
      throw new Error("A service was missing its name or status.");
    }
    services.push({ name, status });
  }
  return services;
}

export default function App() {
  const [services, setServices] = useState([]);
  const [state, setState] = useState("loading");
  const [error, setError] = useState("");
  const [requestNumber, setRequestNumber] = useState(0);

  const retry = useCallback(() => {
    setError("");
    setState("loading");
    setRequestNumber((current) => current + 1);
  }, []);

  useEffect(() => {
    let active = true;
    setState("loading");
    setError("");
    ambient.net.request("status-api", { path: "/v1/status", method: "GET" })
      .then((payload) => {
        if (!active) return;
        const nextServices = readServices(payload);
        setServices(nextServices);
        setState(nextServices.length ? "success" : "empty");
      })
      .catch((reason) => {
        if (!active) return;
        setServices([]);
        setError(reason && typeof reason.message === "string" ? reason.message : "Unable to load service status.");
        setState("error");
      });
    return () => { active = false; };
  }, [requestNumber]);

  return ambient.html`
    <${Card} title="Service Status">
      <${Column} gap="12px">
        ${state === "loading" ? ambient.html`<${Text} text="Loading service status…" />` : null}
        ${state === "error" ? ambient.html`
          <${Column} gap="8px">
            <${Text} text=${`Could not load service status: ${error}`} />
            <${Button} label="Retry" onClick=${retry} />
          <//>
        ` : null}
        ${state === "empty" ? ambient.html`<${Text} text="No services were reported." />` : null}
        ${state === "success" ? services.map((service) => ambient.html`
          <${Row} key=${service.name} gap="12px">
            <${Text} text=${service.name} />
            <${Text} text=${service.status} />
          <//>
        `) : null}
      <//>
    <//>
  `;
}
