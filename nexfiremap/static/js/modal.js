/* In-page replacements for window.prompt / window.confirm.

   The incident-command workflows ran on native dialogs: naming an incident, an
   operational period, a scenario or a snapshot, recovering a crash draft,
   deleting an object, acknowledging a screening warning, approving over
   unresolved warnings, staging a package merge. During a live audit one of them
   froze the whole page.

   Native dialogs are the wrong tool here for reasons that are specific rather
   than aesthetic:

   * They block the event loop. Nothing else in the page runs while one is open
     - not the 15-second telemetry refresh, not the safety-warning poll, not the
     stale-data banner. An operator who walks away from an open prompt comes
     back to a map that stopped updating, with no sign that it did.
   * They cannot be styled or sized, so on a field tablet they render at the
     platform's own scale, which is small, dense, and easy to mis-tap with
     gloves - the exact conditions this application is built for.
   * `prompt()` is unavailable or silently stubbed in several embedded and
     kiosk browsers, which are plausible deployments for a wall display.
   * A cancel loses whatever was typed, with no way to recover it.

   The replacements are promise-based, so a call site changes from
   `const name = prompt(...)` to `const name = await askText(...)` and keeps its
   shape. They resolve to the same values the natives did - `null` for a
   cancelled text prompt, `false` for a declined confirmation - so no caller has
   to learn a new contract.

   One `<dialog>` element is reused for every ask. It is created on first use
   and never removed, matching how the context menu and the auth dialog already
   work in this app. */

/** @type {?HTMLDialogElement} */
let dialog = null;

/** @param {string} value @returns {string} HTML-escaped text. */
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[character]));
}

/** Builds (once) and returns the shared dialog element. */
function ensureDialog() {
  if (dialog) return dialog;
  dialog = document.createElement("dialog");
  dialog.className = "nf-modal";
  dialog.setAttribute("aria-labelledby", "nf-modal-title");
  document.body.appendChild(dialog);
  return dialog;
}

/** Renders one ask and resolves when the operator answers.
 *
 * `fields` is a list of {name, label, value, type, options}; an empty list
 * makes this a confirmation rather than a form.
 * @param {{title: string, body?: string, fields?: Array<object>, confirmLabel?: string,
 *          cancelLabel?: string, danger?: boolean}} spec
 * @returns {Promise<?object>} field values keyed by name, or null if cancelled. */
function ask(spec) {
  const host = ensureDialog();
  const fields = spec.fields || [];

  const controls = fields.map((field, index) => {
    const id = `nf-modal-field-${index}`;
    if (field.options) {
      const options = field.options.map((option) => {
        const value = typeof option === "string" ? option : option.value;
        const label = typeof option === "string" ? option : option.label;
        return `<option value="${escapeHtml(value)}"${value === field.value ? " selected" : ""}>${
          escapeHtml(label)}</option>`;
      }).join("");
      return `<label class="field"><span>${escapeHtml(field.label)}</span>
        <select id="${id}" data-field="${escapeHtml(field.name)}">${options}</select></label>`;
    }
    const type = field.type === "textarea" ? "textarea" : "input";
    if (type === "textarea") {
      return `<label class="field"><span>${escapeHtml(field.label)}</span>
        <textarea id="${id}" data-field="${escapeHtml(field.name)}" rows="3"
          placeholder="${escapeHtml(field.placeholder || "")}">${escapeHtml(field.value || "")}</textarea></label>`;
    }
    return `<label class="field"><span>${escapeHtml(field.label)}</span>
      <input id="${id}" data-field="${escapeHtml(field.name)}" type="text"
        value="${escapeHtml(field.value || "")}"
        placeholder="${escapeHtml(field.placeholder || "")}" maxlength="${field.maxLength || 300}"></label>`;
  }).join("");

  host.innerHTML = `
    <form method="dialog" class="nf-modal-form">
      <h2 id="nf-modal-title">${escapeHtml(spec.title)}</h2>
      ${spec.body ? `<p class="nf-modal-body">${escapeHtml(spec.body).replace(/\n/g, "<br>")}</p>` : ""}
      ${controls}
      <div class="nf-modal-actions">
        <button type="button" class="btn ghost" data-cancel>${escapeHtml(spec.cancelLabel || "Cancel")}</button>
        <button type="submit" class="btn${spec.danger ? " danger" : ""}" data-confirm>${
          escapeHtml(spec.confirmLabel || "OK")}</button>
      </div>
    </form>`;

  return new Promise((resolve) => {
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      host.removeEventListener("close", onClose);
      if (host.open) host.close();
      resolve(value);
    };
    // Covers Escape and any other native close path, so a dismissed dialog
    // always resolves rather than leaving its caller awaiting forever.
    const onClose = () => finish(null);
    host.addEventListener("close", onClose);

    host.querySelector("[data-cancel]").addEventListener("click", () => finish(null));
    host.querySelector("form").addEventListener("submit", (event) => {
      event.preventDefault();
      const values = {};
      host.querySelectorAll("[data-field]").forEach((control) => {
        values[control.dataset.field] = control.value;
      });
      finish(values);
    });

    host.showModal();
    const first = host.querySelector("[data-field]") || host.querySelector("[data-confirm]");
    first?.focus();
    if (first && first.select) first.select();
  });
}

/** A yes/no question. Replaces `window.confirm`.
 * @param {string} message - Shown as the body; the first line becomes the title.
 * @param {{confirmLabel?: string, cancelLabel?: string, danger?: boolean, title?: string}} [options]
 * @returns {Promise<boolean>} */
export async function askConfirm(message, options = {}) {
  const text = String(message ?? "");
  // Several call sites pass a multi-line string whose first line reads as a
  // heading; keeping that split gives the dialog a real title instead of a
  // wall of text, without every call site having to be rewritten.
  const [first, ...rest] = text.split("\n");
  const answer = await ask({
    title: options.title || (rest.length ? first : "Confirm"),
    body: options.title ? text : (rest.length ? rest.join("\n").trim() : first),
    fields: [],
    confirmLabel: options.confirmLabel || "OK",
    cancelLabel: options.cancelLabel,
    danger: options.danger,
  });
  return answer !== null;
}

/** A single text answer. Replaces `window.prompt`.
 * @param {string} label - The question.
 * @param {string} [value] - Prefilled answer, as prompt()'s second argument.
 * @param {{title?: string, placeholder?: string, confirmLabel?: string, multiline?: boolean}} [options]
 * @returns {Promise<?string>} the answer, or null if cancelled - same as prompt(). */
export async function askText(label, value = "", options = {}) {
  const answer = await ask({
    title: options.title || label,
    body: options.title ? label : "",
    fields: [{
      name: "value", label: options.title ? label : "", value,
      placeholder: options.placeholder, type: options.multiline ? "textarea" : "text",
    }],
    confirmLabel: options.confirmLabel || "OK",
  });
  return answer === null ? null : answer.value;
}

/** Several answers at once, so a two-question flow is one dialog.
 *
 * `prompt()` could only ask one thing, so creating an incident meant two
 * consecutive modal interruptions and a scenario meant two more - and
 * cancelling the second threw away the first. One form asks once.
 * @param {string} title
 * @param {Array<{name: string, label: string, value?: string, options?: Array}>} fields
 * @param {{confirmLabel?: string, body?: string}} [options]
 * @returns {Promise<?object>} values keyed by field name, or null if cancelled. */
export async function askForm(title, fields, options = {}) {
  return ask({ title, body: options.body, fields, confirmLabel: options.confirmLabel || "Save" });
}
