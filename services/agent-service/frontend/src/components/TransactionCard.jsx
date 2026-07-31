import { useEffect, useState } from "react";
import { CheckCircle2, Loader2, Send } from "lucide-react";
import { api, newRequestId } from "../api.js";
import { errorMessage, lifecycleText } from "../utils.js";

function FieldControl({ field, value, customActive, onChange, onCustomMode }) {
  const options = Array.isArray(field.options) ? field.options : [];
  const allowCustom = Boolean(field.allow_custom);
  const optionValues = new Set(options.map((option) => String(option.value)));
  const isCustom = allowCustom && value && !optionValues.has(String(value));
  const selected = customActive || isCustom ? "__custom__" : String(value || "");

  if (field.control === "textarea") {
    return <textarea value={value || ""} placeholder={field.placeholder || ""} onChange={(event) => onChange(field.name, event.target.value)} />;
  }
  if (field.control === "select" || field.control === "choice_or_text") {
    return (
      <div className="choice-field">
        <select
          value={selected}
          onChange={(event) => {
            const nextValue = event.target.value;
            if (nextValue === "__custom__") {
              onCustomMode(field.name, true);
              onChange(field.name, "");
              return;
            }
            onCustomMode(field.name, false);
            onChange(field.name, nextValue);
          }}
        >
          <option value="">请选择</option>
          {options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label || option.value}
            </option>
          ))}
          {allowCustom ? <option value="__custom__">其他</option> : null}
        </select>
        {allowCustom && selected === "__custom__" ? (
          <input value={value || ""} placeholder={field.placeholder || ""} onChange={(event) => onChange(field.name, event.target.value)} />
        ) : null}
      </div>
    );
  }
  if (field.control === "checkbox") {
    return <input type="checkbox" checked={Boolean(value)} onChange={(event) => onChange(field.name, event.target.checked)} />;
  }
  return (
    <input
      type={field.control === "number" ? "number" : field.control === "date" ? "date" : "text"}
      value={value || ""}
      placeholder={field.placeholder || ""}
      onChange={(event) => onChange(field.name, event.target.value)}
    />
  );
}

export function TransactionCard({ interaction, update, threadId, token, onResponse, onClear, onError }) {
  const [values, setValues] = useState({});
  const [customFields, setCustomFields] = useState({});
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const next = {};
    const nextCustomFields = {};
    for (const field of interaction?.fields || []) {
      next[field.name] = field.value || "";
      const options = Array.isArray(field.options) ? field.options : [];
      const optionValues = new Set(options.map((option) => String(option.value)));
      nextCustomFields[field.name] = Boolean(field.allow_custom && field.value && !optionValues.has(String(field.value)));
    }
    setValues(next);
    setCustomFields(nextCustomFields);
  }, [interaction?.interaction_id]);

  const lifecycle = update?.lifecycle || interaction?.lifecycle;
  const isInput = lifecycle === "collecting_input";
  const isAuthority = lifecycle === "awaiting_authority";
  const readOnly = Boolean(update || interaction?.read_only || (!isInput && !isAuthority));
  const awaitingUser = isInput || isAuthority;
  const declaredTitle = update?.title || interaction?.title;
  const title = !awaitingUser && (!declaredTitle || declaredTitle === "待办理事务")
    ? "办理完成"
    : declaredTitle || "待办理事务";
  const control = interaction?.control || {};

  function changeValue(name, value) {
    setValues((current) => ({ ...current, [name]: value }));
  }

  function changeCustomMode(name, enabled) {
    setCustomFields((current) => ({ ...current, [name]: enabled }));
  }

  async function submitInput(mode = "submit_input") {
    setBusy(true);
    onError("");
    try {
      const response = await api.submitInput(token, {
        thread_id: threadId,
        interaction_mode: mode,
        offer_handle: control.offer_handle,
        action_id: control.action_id,
        target_handle: control.target_handle,
        form_id: control.form_id,
        form_version: Number(control.form_version || 1),
        form_step: Number(control.form_step || interaction.current_step || 1),
        conversation_revision: Number(control.conversation_revision || 1),
        client_request_id: newRequestId(mode),
        input_values: mode === "submit_input" ? values : {}
      });
      onResponse(response);
    } catch (err) {
      onError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function submitAuthority(decision) {
    setBusy(true);
    onError("");
    try {
      const response = await api.submitAuthority(token, {
        thread_id: threadId,
        decision,
        authority_type: decision === "approved" ? "ui_confirmed" : "ui_rejected",
        offer_handle: control.offer_handle,
        action_id: control.action_id,
        target_handle: control.target_handle,
        confirmation_id: control.confirmation_id,
        confirmation_version: Number(control.confirmation_version || 1),
        conversation_revision: Number(control.conversation_revision || 1),
        client_request_id: newRequestId(decision),
        comment: ""
      });
      onResponse(response);
    } catch (err) {
      onError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className={`transaction-card ${lifecycle || ""}`}>
      <div className="transaction-head">
        <div>
          <div className="eyebrow">{awaitingUser ? "待办理事务" : "办理结果"}</div>
          <h2>{title}</h2>
        </div>
        <span className="status-pill">{lifecycleText(lifecycle)}</span>
      </div>
      {interaction?.target ? <p className="muted">{interaction.target}</p> : null}
      {interaction?.summary || update?.message ? <p>{interaction?.summary || update?.message}</p> : null}
      {Array.isArray(interaction?.details) && interaction.details.length ? (
        <div className="detail-grid">
          {interaction.details.map((row) => (
            <div key={`${row.label}:${row.value}`}>
              <span>{row.label}</span>
              <strong>{row.value}</strong>
            </div>
          ))}
        </div>
      ) : null}
      {isInput ? (
        <div className="form-grid">
          {(interaction.fields || []).filter((field) => Number(field.step || 1) === Number(interaction.current_step || 1)).map((field) => (
            <label key={field.name}>
              {field.label}
              <FieldControl
                field={field}
                value={values[field.name]}
                customActive={Boolean(customFields[field.name])}
                onChange={changeValue}
                onCustomMode={changeCustomMode}
              />
              {field.suggested_value ? <small className="muted">建议：{field.suggested_value}</small> : null}
              {field.error ? <small className="field-error">{field.error}</small> : null}
            </label>
          ))}
        </div>
      ) : null}
      <div className="action-row">
        {isInput ? (
          <>
            <button className="primary-button" onClick={() => submitInput("submit_input")} disabled={busy}>
              {busy ? <Loader2 className="spin" size={16} /> : <Send size={16} />}
              {interaction.current_step < interaction.total_steps ? "下一步" : "继续"}
            </button>
            <button className="ghost-button" onClick={() => submitInput("cancel_interaction")} disabled={busy}>
              暂不办理
            </button>
          </>
        ) : null}
        {isAuthority ? (
          <>
            <button className="primary-button" onClick={() => submitAuthority("approved")} disabled={busy}>
              {busy ? <Loader2 className="spin" size={16} /> : <CheckCircle2 size={16} />}
              确认提交
            </button>
            <button className="ghost-button" onClick={() => submitAuthority("rejected")} disabled={busy}>
              暂不提交
            </button>
          </>
        ) : null}
        {readOnly ? (
          <button className="ghost-button" onClick={onClear}>
            收起
          </button>
        ) : null}
      </div>
    </section>
  );
}
