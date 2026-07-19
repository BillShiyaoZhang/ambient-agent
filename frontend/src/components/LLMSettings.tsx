import React, { useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, ChevronDown, LoaderCircle, Pencil, Plus, RefreshCw, Search, Settings2, Trash2, X } from "lucide-react";
import type { LLMModel, LLMProvider, LLMSettings, ModelSelection, ProviderPreset } from "../services/llm";
import { SystemDialog, SystemIconButton } from "./system/SystemUI";
import "./LLMSettings.css";

interface ModelPickerProps {
  providers: LLMProvider[];
  value: ModelSelection | null;
  onChange: (value: ModelSelection) => void;
  language: "zh" | "en";
  onManage?: () => void;
  label?: string;
  disabled?: boolean;
}

function modelLabel(providers: LLMProvider[], value: ModelSelection | null, language: "zh" | "en") {
  if (!value) return language === "zh" ? "选择模型" : "Select model";
  const model = providers.find((provider) => provider.id === value.provider_id)?.models.find((item) => item.id === value.model_id);
  return model?.display_name || model?.id || value.model_id;
}

export function ModelPicker({ providers, value, onChange, language, onManage, label, disabled }: ModelPickerProps) {
  const isZh = language === "zh";
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const filtered = useMemo(() => providers.filter((provider) => provider.enabled).map((provider) => ({
    ...provider,
    models: provider.models.filter((model) => `${model.display_name ?? ""} ${model.id}`.toLowerCase().includes(query.toLowerCase())),
  })).filter((provider) => provider.models.length > 0), [providers, query]);

  return <div className="llm-model-picker">
    {label ? <span className="llm-field-label">{label}</span> : null}
    <button type="button" className="llm-model-trigger" aria-label={modelLabel(providers, value, language)} disabled={disabled} onClick={() => setOpen((shown) => !shown)}>
      <span>{modelLabel(providers, value, language)}</span><ChevronDown size={13} />
    </button>
    {open ? <div className="llm-model-menu" role="dialog" aria-label={isZh ? "选择模型" : "Select model"}>
      <label className="llm-search"><Search size={14} /><input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder={isZh ? "搜索模型" : "Search models"} /></label>
      <div className="llm-model-groups">
        {filtered.map((provider) => <section key={provider.id}>
          <h4>{provider.name}</h4>
          {provider.models.map((model) => {
            const selected = value?.provider_id === provider.id && value.model_id === model.id;
            const toolUnknown = model.capabilities?.tool_calling !== true;
            return <button type="button" className={selected ? "is-selected" : ""} key={model.id} onClick={() => { onChange({ provider_id: provider.id, model_id: model.id }); setOpen(false); }}>
              <span><strong>{model.display_name || model.id}</strong><small>{model.id}</small></span>
              {toolUnknown ? <em><AlertTriangle size={12} />{isZh ? "工具调用未验证" : "Tool use not verified"}</em> : <CheckCircle2 size={14} />}
            </button>;
          })}
        </section>)}
        {filtered.length === 0 ? <p className="llm-empty">{isZh ? "没有匹配的模型" : "No matching models"}</p> : null}
      </div>
      {onManage ? <button type="button" className="llm-manage-link" onClick={() => { setOpen(false); onManage(); }}><Settings2 size={14} />{isZh ? "管理 Provider" : "Manage providers"}</button> : null}
    </div> : null}
  </div>;
}

interface LLMSettingsDialogProps {
  open: boolean;
  language: "zh" | "en";
  catalog: ProviderPreset[];
  providers: LLMProvider[];
  settings: LLMSettings;
  onClose: () => void;
  onRefresh: () => void | Promise<void>;
  onCreateProvider?: (profile: Record<string, unknown>, credentials: Record<string, unknown>) => Promise<unknown>;
  onUpdateProvider?: (providerId: string, profile: Record<string, unknown>, credentials?: Record<string, unknown>) => Promise<unknown>;
  onDeleteProvider?: (providerId: string) => Promise<unknown>;
  onDiscoverModels?: (providerId: string) => Promise<unknown>;
  onTestProvider?: (providerId: string, modelId?: string, mode?: "connection" | "tools") => Promise<unknown>;
  onUpdateSettings?: (patch: Partial<LLMSettings>) => Promise<unknown>;
}

type Notice = { tone: "success" | "error"; text: string } | null;

export function LLMSettingsDialog(props: LLMSettingsDialogProps) {
  const { open, language, catalog, providers, settings, onClose, onRefresh } = props;
  const isZh = language === "zh";
  const [adding, setAdding] = useState(false);
  const [presetId, setPresetId] = useState(catalog[0]?.id ?? "openai");
  const [name, setName] = useState("");
  const [providerId, setProviderId] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [credentialSources, setCredentialSources] = useState<Record<string, "stored" | "env">>({});
  const [manualModels, setManualModels] = useState<Record<string, string>>({});
  const [editingProvider, setEditingProvider] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editEnabled, setEditEnabled] = useState(true);
  const [editValues, setEditValues] = useState<Record<string, string>>({});
  const [editSources, setEditSources] = useState<Record<string, "stored" | "env">>({});
  const [clearCredentials, setClearCredentials] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const preset = catalog.find((item) => item.id === presetId) ?? catalog[0];
  const presetFields = preset ? [...preset.fields, ...(preset.advanced_fields ?? [])] : [];

  const run = async (key: string, action: () => Promise<unknown>, success: string) => {
    setBusy(key); setNotice(null);
    try { await action(); await onRefresh(); setNotice({ tone: "success", text: success }); }
    catch (error) { setNotice({ tone: "error", text: error instanceof Error ? error.message : String(error) }); }
    finally { setBusy(null); }
  };

  const submitProvider = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!preset || !props.onCreateProvider) return;
    const connection: Record<string, unknown> = {};
    const credential_refs: Record<string, unknown> = {};
    const credentials: Record<string, unknown> = {};
    for (const field of presetFields) {
      if (field.secret) {
        const source = credentialSources[field.id] ?? "stored";
        if (values[field.id]) {
          credential_refs[field.id] = source === "env" ? { source, env_var: values[field.id] } : { source };
          if (source === "stored") credentials[field.id] = { source, value: values[field.id] };
          if (source === "env") credentials[field.id] = { source, env_var: values[field.id] };
        }
      } else if (values[field.id]) {
        if (field.kind === "json") {
          try {
            const parsed = JSON.parse(values[field.id]);
            if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error("not an object");
            connection[field.id] = parsed;
          } catch {
            setNotice({ tone: "error", text: isZh ? `${field.label} 必须是 JSON 对象` : `${field.label} must be a JSON object` });
            return;
          }
        } else if (field.kind === "number") connection[field.id] = Number(values[field.id]);
        else connection[field.id] = values[field.id];
      }
    }
    const id = providerId.trim() || `${preset.id}-${Date.now().toString(36)}`;
    await run("create", () => props.onCreateProvider!({ id, name: name.trim() || preset.name, preset: preset.id, enabled: true, connection, credential_refs, models: [] }, credentials), isZh ? "Provider 已添加" : "Provider added");
    setAdding(false); setName(""); setProviderId(""); setValues({}); setCredentialSources({});
  };

  const addManualModel = async (provider: LLMProvider) => {
    const modelId = manualModels[provider.id]?.trim();
    if (!modelId || !props.onUpdateProvider) return;
    const models = [...provider.models.filter((model) => model.id !== modelId), { id: modelId, display_name: modelId, source: "manual" as const, capabilities: { verification: "unknown" as const } }];
    await run(`manual-${provider.id}`, () => props.onUpdateProvider!(provider.id, { models }), isZh ? "模型已添加" : "Model added");
    setManualModels((current) => ({ ...current, [provider.id]: "" }));
  };

  const beginEdit = (provider: LLMProvider) => {
    const providerPreset = catalog.find((item) => item.id === provider.preset);
    const fields = providerPreset ? [...providerPreset.fields, ...(providerPreset.advanced_fields ?? [])] : [];
    const nextValues: Record<string, string> = {};
    const nextSources: Record<string, "stored" | "env"> = {};
    for (const field of fields) {
      if (field.secret) {
        const credential = provider.credentials?.[field.id];
        nextSources[field.id] = credential?.source ?? "stored";
        if (credential?.source === "env" && credential.env_var) nextValues[field.id] = credential.env_var;
      } else {
        const value = provider.connection[field.id];
        if (value !== undefined && value !== null) nextValues[field.id] = typeof value === "object" ? JSON.stringify(value) : String(value);
      }
    }
    setEditingProvider(provider.id); setEditName(provider.name); setEditEnabled(provider.enabled);
    setEditValues(nextValues); setEditSources(nextSources); setClearCredentials({});
  };

  const saveProviderEdit = async (provider: LLMProvider) => {
    if (!props.onUpdateProvider) return;
    const providerPreset = catalog.find((item) => item.id === provider.preset);
    const fields = providerPreset ? [...providerPreset.fields, ...(providerPreset.advanced_fields ?? [])] : [];
    const connection: Record<string, unknown> = {};
    const credentials: Record<string, unknown> = {};
    for (const field of fields) {
      const value = editValues[field.id]?.trim();
      if (field.secret) {
        if (clearCredentials[field.id]) credentials[field.id] = { clear: true };
        else if (value) {
          const source = editSources[field.id] ?? "stored";
          credentials[field.id] = source === "env" ? { source, env_var: value } : { source, value };
        }
      } else if (value) {
        if (field.kind === "json") {
          try {
            const parsed = JSON.parse(value);
            if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error("not object");
            connection[field.id] = parsed;
          } catch {
            setNotice({ tone: "error", text: isZh ? `${field.label} 必须是 JSON 对象` : `${field.label} must be a JSON object` });
            return;
          }
        } else if (field.kind === "number") connection[field.id] = Number(value);
        else connection[field.id] = value;
      }
    }
    await run(`edit-${provider.id}`, () => props.onUpdateProvider!(provider.id, { name: editName.trim() || provider.name, enabled: editEnabled, connection }, Object.keys(credentials).length ? credentials : undefined), isZh ? "Provider 已更新" : "Provider updated");
    setEditingProvider(null);
  };

  return <SystemDialog open={open} size="large" title={isZh ? "模型与 Provider" : "Models & Providers"} description={isZh ? "配置服务端连接，并选择默认与快速模型。" : "Configure server-side connections and choose default and fast models."} onClose={onClose} className="llm-settings-dialog">
    <div className="llm-settings-toolbar">
      <button type="button" className="system-button is-primary" aria-label={isZh ? "添加 Provider" : "Add provider"} onClick={() => setAdding((value) => !value)}><Plus size={15} />{isZh ? "添加 Provider" : "Add provider"}</button>
      <SystemIconButton label={isZh ? "刷新配置" : "Refresh configuration"} onClick={() => void onRefresh()}><RefreshCw size={16} /></SystemIconButton>
      <SystemIconButton label={isZh ? "关闭" : "Close"} onClick={onClose}><X size={17} /></SystemIconButton>
    </div>
    <div className="llm-settings-body">
      {notice ? <div className={`llm-notice is-${notice.tone}`} role="status">{notice.text}</div> : null}
      <section className="llm-defaults">
        <ModelPicker label={isZh ? "默认主模型" : "Default model"} language={language} providers={providers} value={settings.default_model} onChange={(selection) => props.onUpdateSettings && void run("default", () => props.onUpdateSettings!({ default_model: selection }), isZh ? "默认模型已更新" : "Default model updated")} />
        <div className="llm-fast-setting"><ModelPicker label={isZh ? "快速模型（路由/标题）" : "Fast model (routing/titles)"} language={language} providers={providers} value={settings.fast_model} onChange={(selection) => props.onUpdateSettings && void run("fast", () => props.onUpdateSettings!({ fast_model: selection }), isZh ? "快速模型已更新" : "Fast model updated")} />
          {settings.fast_model && props.onUpdateSettings ? <button type="button" onClick={() => void run("fast", () => props.onUpdateSettings!({ fast_model: null }), isZh ? "快速模型将跟随会话" : "Fast model now follows the session")}>{isZh ? "跟随会话主模型" : "Use session model"}</button> : null}
        </div>
        <p>{isZh ? "快速模型未配置时使用当前会话主模型。运行中切换只影响下一次请求。" : "When unset, the fast model follows the session model. Changes during a run apply to the next request."}</p>
      </section>

      {adding && preset ? <form className="llm-add-form" onSubmit={submitProvider}>
        <div className="llm-form-heading"><strong>{isZh ? "新增 Provider" : "New provider"}</strong><span>{isZh ? "自定义端点可能让服务端访问本地或内网地址。" : "Custom endpoints can let the server access local or private-network addresses."}</span></div>
        <label><span>{isZh ? "预设" : "Preset"}</span><select value={presetId} onChange={(event) => { setPresetId(event.target.value); setValues({}); }}>
          {catalog.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.category}</option>)}
        </select></label>
        <label><span>{isZh ? "显示名称" : "Display name"}</span><input value={name} onChange={(event) => setName(event.target.value)} placeholder={preset.name} /></label>
        <label><span>Provider ID</span><input value={providerId} onChange={(event) => setProviderId(event.target.value)} placeholder={`${preset.id}-main`} /></label>
        {presetFields.map((field) => <div className="llm-dynamic-field" key={field.id}>
          <label><span>{field.label}{field.required ? " *" : ""}</span><input required={field.required} type={field.secret && credentialSources[field.id] !== "env" ? "password" : field.kind === "url" ? "url" : "text"} value={values[field.id] ?? (field.id === "base_url" ? preset.default_base_url ?? "" : "")} onChange={(event) => setValues((current) => ({ ...current, [field.id]: event.target.value }))} placeholder={field.secret && credentialSources[field.id] === "env" ? "MY_API_KEY" : ""} /></label>
          {field.secret ? <select aria-label={`${field.label} source`} value={credentialSources[field.id] ?? "stored"} onChange={(event) => setCredentialSources((current) => ({ ...current, [field.id]: event.target.value as "stored" | "env" }))}><option value="stored">{isZh ? "安全存储" : "Stored"}</option><option value="env">{isZh ? "环境变量" : "Environment variable"}</option></select> : null}
        </div>)}
        <div className="llm-form-actions"><button type="button" className="system-button" onClick={() => setAdding(false)}>{isZh ? "取消" : "Cancel"}</button><button disabled={busy === "create" || !props.onCreateProvider} className="system-button is-primary" type="submit">{busy === "create" ? <LoaderCircle className="is-spinning" size={14} /> : null}{isZh ? "保存" : "Save"}</button></div>
      </form> : null}

      <div className="llm-provider-list">
        {providers.map((provider) => <article className="llm-provider-card" key={provider.id}>
          {(() => {
            const configured = settings.default_model?.provider_id === provider.id
              ? settings.default_model.model_id
              : settings.fast_model?.provider_id === provider.id
                ? settings.fast_model.model_id
                : undefined;
            const testModelId = provider.models.some((model) => model.id === configured)
              ? configured
              : provider.models.find((model) => model.capabilities?.tool_calling === true)?.id
                ?? provider.models[0]?.id;
            return <>
          <header><div><strong>{provider.name}</strong><span>{provider.preset} · {provider.id}</span></div><span className={provider.enabled ? "is-enabled" : ""}>{provider.enabled ? (isZh ? "已启用" : "Enabled") : (isZh ? "已停用" : "Disabled")}</span></header>
          {provider.credentials && Object.entries(provider.credentials).length ? <div className="llm-credential-list">{Object.entries(provider.credentials).map(([key, credential]) => <span key={key}><small>{key}</small>{credential.masked || credential.env_var || (credential.configured ? (isZh ? "已配置" : "Configured") : (isZh ? "未配置" : "Not configured"))}</span>)}</div> : null}
          <div className="llm-model-tags">{provider.models.map((model: LLMModel) => <span key={model.id}>{model.display_name || model.id}{model.capabilities?.tool_calling !== true ? <AlertTriangle size={11} /> : null}{props.onUpdateProvider ? <button type="button" aria-label={isZh ? `删除模型 ${model.id}` : `Delete model ${model.id}`} onClick={() => void run(`remove-model-${provider.id}`, () => props.onUpdateProvider!(provider.id, { models: provider.models.filter((item) => item.id !== model.id) }), isZh ? "模型已删除" : "Model removed")}><X size={10} /></button> : null}</span>)}{provider.models.length === 0 ? <em>{isZh ? "尚无模型" : "No models yet"}</em> : null}</div>
          {editingProvider === provider.id ? <div className="llm-edit-form">
            <label><span>{isZh ? "显示名称" : "Display name"}</span><input value={editName} onChange={(event) => setEditName(event.target.value)} /></label>
            <label className="llm-enable-row"><input type="checkbox" checked={editEnabled} onChange={(event) => setEditEnabled(event.target.checked)} />{isZh ? "启用 Provider" : "Provider enabled"}</label>
            {(() => {
              const providerPreset = catalog.find((item) => item.id === provider.preset);
              return providerPreset ? [...providerPreset.fields, ...(providerPreset.advanced_fields ?? [])].map((field) => <div className="llm-edit-field" key={field.id}>
                <label><span>{field.label}</span><input type={field.secret && editSources[field.id] !== "env" ? "password" : "text"} value={editValues[field.id] ?? ""} onChange={(event) => { setEditValues((current) => ({ ...current, [field.id]: event.target.value })); setClearCredentials((current) => ({ ...current, [field.id]: false })); }} placeholder={field.secret ? (provider.credentials?.[field.id]?.masked || (isZh ? "保持不变" : "Leave unchanged")) : ""} /></label>
                {field.secret ? <><select value={editSources[field.id] ?? provider.credentials?.[field.id]?.source ?? "stored"} onChange={(event) => setEditSources((current) => ({ ...current, [field.id]: event.target.value as "stored" | "env" }))}><option value="stored">{isZh ? "安全存储" : "Stored"}</option><option value="env">{isZh ? "环境变量" : "Environment variable"}</option></select><button type="button" className={clearCredentials[field.id] ? "is-active" : ""} onClick={() => { setClearCredentials((current) => ({ ...current, [field.id]: true })); setEditValues((current) => ({ ...current, [field.id]: "" })); }}>{isZh ? "清除" : "Clear"}</button></> : null}
              </div>) : null;
            })()}
            <div className="llm-form-actions"><button type="button" className="system-button" onClick={() => setEditingProvider(null)}>{isZh ? "取消" : "Cancel"}</button><button type="button" className="system-button is-primary" onClick={() => void saveProviderEdit(provider)}>{isZh ? "保存更改" : "Save changes"}</button></div>
          </div> : null}
          <div className="llm-manual-row"><input value={manualModels[provider.id] ?? ""} onChange={(event) => setManualModels((current) => ({ ...current, [provider.id]: event.target.value }))} placeholder={isZh ? "手动输入 model ID" : "Enter model ID manually"} /><button type="button" onClick={() => void addManualModel(provider)}>{isZh ? "添加" : "Add"}</button></div>
          <footer>
            <button type="button" disabled={!props.onUpdateProvider} onClick={() => editingProvider === provider.id ? setEditingProvider(null) : beginEdit(provider)}><Pencil size={13} />{isZh ? "编辑" : "Edit"}</button>
            <button type="button" disabled={!props.onDiscoverModels || busy === `discover-${provider.id}`} onClick={() => props.onDiscoverModels && void run(`discover-${provider.id}`, () => props.onDiscoverModels!(provider.id), isZh ? "模型列表已刷新" : "Models refreshed")}><RefreshCw size={13} />{isZh ? "发现模型" : "Discover"}</button>
            <button type="button" disabled={!props.onTestProvider || !testModelId || busy === `test-${provider.id}`} onClick={() => props.onTestProvider && void run(`test-${provider.id}`, () => props.onTestProvider!(provider.id, testModelId), isZh ? "连接测试成功" : "Connection successful")}><CheckCircle2 size={13} />{isZh ? "测试连接" : "Test"}</button>
            <button type="button" disabled={!props.onTestProvider || !testModelId} onClick={() => props.onTestProvider && void run(`tools-${provider.id}`, () => props.onTestProvider!(provider.id, testModelId, "tools"), isZh ? "工具调用已验证" : "Tool calling verified")}><CheckCircle2 size={13} />{isZh ? "验证工具" : "Test tools"}</button>
            <button type="button" className="is-danger" disabled={!props.onDeleteProvider} onClick={() => { if (props.onDeleteProvider && window.confirm(isZh ? `删除 ${provider.name}？` : `Delete ${provider.name}?`)) void run(`delete-${provider.id}`, () => props.onDeleteProvider!(provider.id), isZh ? "Provider 已删除" : "Provider deleted"); }}><Trash2 size={13} />{isZh ? "删除" : "Delete"}</button>
          </footer>
            </>;
          })()}
        </article>)}
        {providers.length === 0 ? <div className="llm-empty-state"><Settings2 size={24} /><strong>{isZh ? "还没有 Provider" : "No providers configured"}</strong><p>{isZh ? "添加 Provider 后才能启动 Agent 任务。" : "Add a provider before starting an agent task."}</p></div> : null}
      </div>
    </div>
  </SystemDialog>;
}
