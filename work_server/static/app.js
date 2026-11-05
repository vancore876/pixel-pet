"use strict";

(() => {
  const SUPABASE_FUNCTION_URL = window.JEFFERY_SUPABASE_FUNCTION_URL;
  const SUPABASE_REGISTER_URL = window.JEFFERY_SUPABASE_REGISTER_URL;
  const SUPABASE_ANON_KEY = window.JEFFERY_SUPABASE_ANON_KEY;
  if (typeof SUPABASE_FUNCTION_URL !== "string" || typeof SUPABASE_REGISTER_URL !== "string" || typeof SUPABASE_ANON_KEY !== "string") throw new Error("Jeffery cloud configuration is missing.");
  const apiUrl = path => SUPABASE_FUNCTION_URL + path.replace(/^\/api(?=\/|\?|$)/, "");
  const $ = (id) => document.getElementById(id);
  const state = {
    token: "", user: null, users: [], peer: null, cursor: 0, messages: new Map(),
    drafts: new Map(), authEpoch: 0, conversationEpoch: 0, requests: new Set(),
    pollTimer: null, usersTimer: null, dataTimer: null, pollBusy: null, sending: false, signingUp: false,
    business: [], businessCursor: 0, businessLoading: false, selectedNoteId: "", selectedOrderId: "", inventory: [], inventorySearchSequence: 0, inventorySearchTimer: null, draftLines: [], receivingMatches: [], receivingPreviewId: "",
  };
  const conversationKey = (peer = state.peer) => peer === null ? "team" : String(peer);
  const newHexId = () => {
    if (window.crypto?.randomUUID) return crypto.randomUUID().replaceAll("-", "");
    return Array.from({length: 32}, () => Math.floor(Math.random() * 16).toString(16)).join("");
  };
  $("server-name").textContent = location.host;

  function status(text, error = false) {
    $("connection-status").textContent = text;
    $("connection-status").classList.toggle("error-status", error);
  }

  async function request(path, { method = "GET", body, token = state.token, endpoint = SUPABASE_FUNCTION_URL } = {}) {
    const controller = new AbortController();
    state.requests.add(controller);
    const timer = setTimeout(() => controller.abort(), 10000);
    try {
      const headers = { Accept: "application/json" };
      if (body !== undefined) headers["Content-Type"] = "application/json";
      headers.apikey = SUPABASE_ANON_KEY;
      headers.Authorization = `Bearer ${token || SUPABASE_ANON_KEY}`;
      const response = await fetch(endpoint === SUPABASE_FUNCTION_URL ? apiUrl(path) : endpoint + path, {
        method, headers, body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal, credentials: "omit", cache: "no-store", redirect: "error",
      });
      if (response.status === 204) return null;
      const reader = response.body.getReader();
      const chunks = [];
      let length = 0;
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        length += value.byteLength;
        if (length > 1024 * 1024) {
          controller.abort();
          throw new Error("The server response is too large.");
        }
        chunks.push(value);
      }
      const bytes = new Uint8Array(length);
      let offset = 0;
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
      let data;
      try { data = JSON.parse(new TextDecoder().decode(bytes)); }
      catch { throw new Error("The server returned an unreadable response."); }
      if (!response.ok) {
        if (response.status === 401 && token && state.token === token) {
          endSession("Your session has ended. Sign in again.");
        }
        throw new Error(typeof data.detail === "string" ? data.detail : `Request failed (${response.status}).`);
      }
      return data;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("The connection timed out. Try again.");
      if (error instanceof TypeError) throw new Error("Cannot reach Supabase. Check your internet connection.");
      throw error;
    } finally {
      clearTimeout(timer);
      state.requests.delete(controller);
    }
  }

  function accountMode(signingUp) {
    state.signingUp = signingUp;
    $("signin-tab").classList.toggle("selected", !signingUp);
    $("signup-tab").classList.toggle("selected", signingUp);
    $("signin-tab").setAttribute("aria-pressed", String(!signingUp));
    $("signup-tab").setAttribute("aria-pressed", String(signingUp));
    $("confirm-field").hidden = !signingUp;
    $("signup-hint").hidden = !signingUp;
    $("confirm-password").required = signingUp;
    $("password").minLength = signingUp ? 12 : 1;
    $("password").autocomplete = signingUp ? "new-password" : "current-password";
    $("auth-title").textContent = signingUp ? "Join your team." : "Good to see you.";
    $("auth-subtitle").textContent = signingUp ? "Create your username and password to join the shared workspace." : "Sign in to join your coworkers.";
    $("auth-submit").textContent = signingUp ? "Create account" : "Sign in";
    $("auth-error").textContent = "";
  }

  function endSession(message = "") {
    state.authEpoch++;
    state.conversationEpoch++;
    clearTimeout(state.pollTimer);
    clearInterval(state.usersTimer);
    clearInterval(state.dataTimer);
    for (const controller of state.requests) controller.abort();
    state.token = "";
    state.user = null;
    state.users = [];
    state.messages.clear();
    state.drafts.clear();
    state.pollBusy = null;
    state.business = []; state.businessCursor = 0; state.businessLoading = false;
    state.selectedNoteId = ""; state.selectedOrderId = "";
    state.sending = false;
    $("password").value = "";
    $("confirm-password").value = "";
    $("message-body").value = "";
    $("messages").replaceChildren();
    $("people-list").replaceChildren();
    $("send-message").disabled = false;
    $("chat-view").hidden = true;
    $("auth-view").hidden = false;
    $("manage-coworkers").hidden = true;
    $("auth-error").textContent = message;
  }

  $("signin-tab").addEventListener("click", () => accountMode(false));
  $("signup-tab").addEventListener("click", () => accountMode(true));
  $("auth-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (state.signingUp && $("password").value !== $("confirm-password").value) {
      $("auth-error").textContent = "The passwords do not match.";
      return;
    }
    const epoch = ++state.authEpoch;
    $("auth-submit").disabled = true;
    $("signin-tab").disabled = true;
    $("signup-tab").disabled = true;
    $("auth-error").textContent = "";
    try {
      const signingUp = state.signingUp;
      const data = await request(signingUp ? "" : "/api/auth/login", {
        method: "POST", token: "", endpoint: signingUp ? SUPABASE_REGISTER_URL : SUPABASE_FUNCTION_URL,
        body: { username: $("username").value.trim(), password: $("password").value },
      });
      if (epoch !== state.authEpoch) return;
      if (!data || typeof data.token !== "string" || !Number.isInteger(data.user?.id) || typeof data.user.username !== "string") {
        throw new Error("The server returned an invalid sign-in response.");
      }
      state.token = data.token;
      state.user = data.user;
      $("password").value = "";
      $("confirm-password").value = "";
      $("account-name").textContent = data.user.username;
      $("web-account-name").textContent = data.user.username;
      $("account-avatar").textContent = data.user.username.slice(0, 1).toUpperCase();
      $("manage-coworkers").hidden = data.user.role !== "admin";
      $("auth-view").hidden = true;
      $("chat-view").hidden = false;
      showPanel("chat-panel");
      selectConversation(null);
      await fetchUsers();
      loadBusiness();
      loadInventory();
      if (epoch === state.authEpoch) state.usersTimer = setInterval(fetchUsers, 15000);
      if (epoch === state.authEpoch) state.dataTimer = setInterval(() => {
        if (!document.hidden && state.token) loadBusiness();
      }, 5000);
    } catch (error) {
      if (epoch === state.authEpoch) $("auth-error").textContent = error.message;
    } finally {
      $("auth-submit").disabled = false;
      $("signin-tab").disabled = false;
      $("signup-tab").disabled = false;
    }
  });

  function renderUsers() {
    const search = $("people-search").value.toLowerCase();
    const users = state.users.filter(user => user.id !== state.user?.id && user.username.toLowerCase().includes(search));
    $("people-list").replaceChildren();
    $("people-empty").hidden = users.length > 0;
    $("people-empty").textContent = search ? "No matching coworkers." : "Coworkers will appear here after they create an account.";
    for (const user of users) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "conversation";
      button.classList.toggle("selected", state.peer === user.id);
      button.setAttribute("aria-pressed", String(state.peer === user.id));
      const avatar = document.createElement("span");
      avatar.className = "person-avatar";
      avatar.setAttribute("aria-hidden", "true");
      avatar.textContent = user.username.slice(0, 1).toUpperCase();
      const name = document.createElement("span");
      name.textContent = user.username;
      button.append(avatar, name);
      button.addEventListener("click", () => selectConversation(user.id));
      $("people-list").append(button);
    }
  }

  async function fetchUsers() {
    if (!state.token) return;
    const epoch = state.authEpoch;
    try {
      const data = await request("/api/users");
      if (epoch !== state.authEpoch) return;
      if (!Array.isArray(data?.users)) throw new Error("Cannot read the coworker list.");
      state.users = data.users.filter(user => Number.isInteger(user.id) && typeof user.username === "string");
      renderUsers();
    } catch (error) {
      if (epoch === state.authEpoch && state.token) status(error.message, true);
    }
  }

  function selectConversation(peer) {
    if (state.user) state.drafts.set(conversationKey(), $("message-body").value);
    state.peer = peer;
    state.conversationEpoch++;
    state.cursor = 0;
    state.messages.clear();
    state.pollBusy = null;
    clearTimeout(state.pollTimer);
    $("messages").replaceChildren();
    $("empty-conversation").hidden = false;
    $("team-room").classList.toggle("selected", peer === null);
    $("team-room").setAttribute("aria-pressed", String(peer === null));
    const username = state.users.find(user => user.id === peer)?.username || "Coworker";
    $("conversation-title").textContent = peer === null ? "Team Room" : username;
    $("conversation-subtitle").textContent = peer === null ? "A shared space for your team." : `Direct messages between you and ${username}.`;
    $("message-body").placeholder = peer === null ? "Message the team…" : `Message ${username}…`;
    $("message-body").value = state.drafts.get(conversationKey()) || "";
    renderUsers();
    fetchMessages(true);
  }

  function renderMessages() {
    const scroll = $("messages-scroll");
    const nearBottom = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 100;
    const firstRender = $("messages").childElementCount === 0;
    const messages = [...state.messages.values()].sort((a, b) => a.id - b.id).slice(-500);
    state.messages = new Map(messages.map(message => [message.id, message]));
    $("messages").replaceChildren();
    $("empty-conversation").hidden = messages.length > 0;
    for (const message of messages) {
      const item = document.createElement("li");
      item.className = "message";
      item.classList.toggle("mine", message.sender_id === state.user.id);
      const avatar = document.createElement("span");
      avatar.className = "avatar";
      avatar.setAttribute("aria-hidden", "true");
      avatar.textContent = message.sender_username.slice(0, 1).toUpperCase();
      const content = document.createElement("div");
      content.className = "message-content";
      const meta = document.createElement("div");
      meta.className = "message-meta";
      const author = document.createElement("strong");
      author.textContent = message.sender_username;
      const time = document.createElement("time");
      const date = new Date(message.created_at);
      if (!Number.isNaN(date.valueOf())) {
        time.dateTime = date.toISOString();
        time.textContent = date.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
      }
      const text = document.createElement("div");
      text.className = "message-text";
      text.textContent = message.body;
      meta.append(author, time);
      content.append(meta, text);
      item.append(avatar, content);
      $("messages").append(item);
    }
    if (nearBottom || firstRender) scroll.scrollTop = scroll.scrollHeight;
  }

  function acceptMessages(messages) {
    if (!Array.isArray(messages)) throw new Error("The server returned an invalid message list.");
    let changed = false;
    for (const message of messages) {
      if (!Number.isInteger(message.id) || typeof message.body !== "string" || typeof message.sender_username !== "string") {
        throw new Error("The server returned an invalid message.");
      }
      if (!state.messages.has(message.id)) {
        state.messages.set(message.id, message);
        changed = true;
      }
    }
    if (changed) renderMessages();
  }

  async function fetchMessages(initial = false) {
    if (!state.token || document.hidden) return;
    const epoch = state.authEpoch;
    const conversation = state.conversationEpoch;
    const marker = `${epoch}:${conversation}`;
    if (state.pollBusy === marker) return;
    state.pollBusy = marker;
    clearTimeout(state.pollTimer);
    try {
      let more = true;
      let batches = 0;
      while (more && batches++ < 5) {
        const query = new URLSearchParams({ limit: "50" });
        if (state.peer !== null) query.set("peer_id", String(state.peer));
        if (!initial || state.cursor > 0) query.set("after_id", String(state.cursor));
        const data = await request(`/api/messages?${query}`);
        if (epoch !== state.authEpoch || conversation !== state.conversationEpoch) return;
        acceptMessages(data.messages);
        for (const message of data.messages) state.cursor = Math.max(state.cursor, message.id);
        more = data.has_more === true && data.messages.length > 0;
        initial = false;
      }
      status("Connected · New messages update automatically");
    } catch (error) {
      if (epoch === state.authEpoch && conversation === state.conversationEpoch && state.token) status(`${error.message} Retrying…`, true);
    } finally {
      if (state.pollBusy === marker) state.pollBusy = null;
      if (epoch === state.authEpoch && conversation === state.conversationEpoch && state.token) {
        state.pollTimer = setTimeout(() => fetchMessages(state.cursor === 0), 5000);
      }
    }
  }

  $("message-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const body = $("message-body").value.trim();
    if (!body || state.sending || !state.token) return;
    const epoch = state.authEpoch;
    const conversation = state.conversationEpoch;
    const peer = state.peer;
    const key = conversationKey(peer);
    const draft = $("message-body").value;
    state.drafts.set(key, draft);
    state.sending = true;
    $("send-message").disabled = true;
    try {
      const data = await request("/api/messages", { method: "POST", body: { body, recipient_id: peer } });
      if (epoch !== state.authEpoch) return;
      if (state.drafts.get(key) === draft) state.drafts.set(key, "");
      if (conversation === state.conversationEpoch) {
        if ($("message-body").value === draft) $("message-body").value = "";
        acceptMessages([data.message]);
        // Only GET replies advance the cursor, preserving messages arriving before our send.
        fetchMessages(state.cursor === 0);
      }
    } catch (error) {
      if (epoch === state.authEpoch) status(`${error.message} Your message is still a draft.`, true);
    } finally {
      if (epoch === state.authEpoch) {
        state.sending = false;
        $("send-message").disabled = false;
      }
    }
  });
  $("message-body").addEventListener("input", () => state.drafts.set(conversationKey(), $("message-body").value));
  $("message-body").addEventListener("keydown", (event) => {
    if (event.ctrlKey && event.key === "Enter") { event.preventDefault(); $("message-form").requestSubmit(); }
  });

  function showPanel(id) {
    for (const panel of ["chat-panel", "coworkers-panel", "notes-panel", "orders-panel", "inventory-panel", "receiving-panel", "backup-panel"]) {
      $(panel).hidden = panel !== id;
    }
    document.querySelectorAll(".workspace-tab").forEach(button => {
      const selected = button.dataset.panel === id;
      button.classList.toggle("selected", selected);
      button.setAttribute("aria-pressed", String(selected));
    });
    if (id === "notes-panel" || id === "orders-panel") loadBusiness();
    if (id === "inventory-panel") loadInventory();
  }

  function recordLabel(entry) {
    return [entry.order_ref, entry.customer].filter(Boolean).join(" · ");
  }

  async function loadBusiness() {
    if (!state.token || state.businessLoading) return;
    state.businessLoading = true;
    $("orders-status").textContent = "Loading orders…";
    $("notes-status").textContent = "Loading notes…";
    try {
      const byId = new Map(state.business.map(record => [record.id, record]));
      let recordsChanged = false;
      let cursor = state.businessCursor;
      for (let page = 0; page < 1000; page++) {
        const data = await request(`/api/business?after_revision=${cursor}&limit=3`);
        if (!Array.isArray(data.entries)) throw new Error("Cannot read the shared notebook.");
        for (const record of data.entries) {
          if (record.deleted || !record.entry) recordsChanged = byId.delete(record.id) || recordsChanged;
          else {
            const previous = byId.get(record.id);
            if (!previous || previous.revision !== record.revision) recordsChanged = true;
            byId.set(record.id, record);
          }
        }
        if (!data.has_more) break;
        if (data.cursor <= cursor) throw new Error("The shared notebook returned an invalid cursor.");
        cursor = data.cursor;
      }
      state.businessCursor = Math.max(cursor, state.businessCursor);
      if (recordsChanged || state.business.length === 0) {
        state.business = [...byId.values()];
        renderBusiness();
      }
      const noteCount = state.business.filter(record => record.entry.kind === "note").length;
      const orderCount = state.business.length - noteCount;
      $("notes-status").textContent = `${noteCount} notes`;
      $("orders-status").textContent = `${orderCount} orders and checklists`;
    } catch (error) {
      $("orders-status").textContent = error.message;
      $("notes-status").textContent = error.message;
    } finally {
      state.businessLoading = false;
    }
  }

  function renderBusiness() {
    const records = state.business.slice().sort((a, b) => (b.entry.updated || 0) - (a.entry.updated || 0));
    const searchText = value => String(value || "").toLocaleLowerCase("en-US");
    const noteQuery = searchText($("notes-search").value.trim());
    const orderQuery = searchText($("orders-search").value.trim());
    const notes = records.filter(record => record.entry.kind === "note");
    const orders = records.filter(record => record.entry.kind !== "note");
    const noteRecords = notes.filter(({entry}) => !noteQuery || searchText([entry.title, entry.body, ...(entry.checklist || []).flatMap(row => [row.text, row.part_number])].join(" ")).includes(noteQuery));
    const orderRecords = orders.filter(({entry}) => !orderQuery || searchText([entry.title, entry.customer, entry.order_ref, entry.body, ...(entry.checklist || []).flatMap(row => [row.text, row.part_number])].join(" ")).includes(orderQuery));
    $("notes-status").textContent = noteQuery ? noteRecords.length + " matching notes" : notes.length + " notes";
    $("orders-status").textContent = orderQuery ? orderRecords.length + " matching orders" : orders.length + " orders and checklists";
    const openOrders = orders.filter(({entry}) => entry.kind === "order" && !entry.done && !["delivered", "cancelled"].includes(entry.order_status) && entry.order_type !== "quote");
    const openBalance = openOrders.reduce((sum, {entry}) => sum + orderTotals(entry).balance, 0);
    const activeParts = openOrders.reduce((sum, {entry}) => sum + (entry.checklist || []).reduce((n, row) => n + (Number(row.quantity) || 0), 0), 0);
    $("order-summary").replaceChildren(summaryCard(String(openOrders.length), "Open customer orders"), summaryCard(formatMoney(openBalance), "Outstanding balance (JMD)"), summaryCard(String(activeParts), "Parts on open orders"));

    if (!noteRecords.some(record => record.id === state.selectedNoteId)) state.selectedNoteId = noteRecords[0]?.id || "";
    if (!orderRecords.some(record => record.id === state.selectedOrderId)) state.selectedOrderId = orderRecords[0]?.id || "";
    renderRecordRail($("notes-list"), noteRecords, state.selectedNoteId, "note");
    renderRecordRail($("orders-list"), orderRecords, state.selectedOrderId, "order");
    renderRecordDetail($("note-detail"), notes.find(record => record.id === state.selectedNoteId), "note");
    renderRecordDetail($("order-detail"), orders.find(record => record.id === state.selectedOrderId), "order");
  }

  function renderRecordRail(host, records, selectedId, kind) {
    host.replaceChildren();
    if (!records.length) {
      const empty = document.createElement("div"); empty.className = "rail-empty";
      empty.textContent = kind === "note" ? "No notes found. Create a note to get started." : "No orders found. Create an order to get started.";
      host.append(empty); return;
    }
    for (const record of records) {
      const entry = record.entry;
      const button = document.createElement("button"); button.type = "button"; button.className = "record-row";
      const selected = record.id === selectedId;
      button.classList.toggle("selected", selected); button.setAttribute("aria-current", String(selected));
      const top = document.createElement("span"); top.className = "record-row-top";
      const title = document.createElement("strong"); title.textContent = entry.title || "Untitled entry";
      const badge = document.createElement("span"); badge.className = "status-pill";
      badge.textContent = kind === "note" ? "note" : (entry.order_status || (entry.kind === "list" ? "checklist" : "order"));
      top.append(title, badge);
      const meta = document.createElement("span"); meta.className = "record-row-meta"; meta.textContent = recordLabel(entry) || "Shared workspace";
      const previewText = entry.body || (entry.checklist || []).slice(0, 2).map(row => row.part_number || row.text).join(" | ") || "No details yet";
      const preview = document.createElement("span"); preview.className = "record-row-preview"; preview.textContent = previewText;
      button.append(top, meta, preview);
      if (entry.kind === "order") {
        const totals = orderTotals(entry); const amount = document.createElement("span"); amount.className = "record-row-balance";
        amount.textContent = totals.credit ? "Credit " + formatMoney(totals.credit) : "Due " + formatMoney(totals.balance);
        button.append(amount);
      }
      button.addEventListener("click", () => {
        if (kind === "note") state.selectedNoteId = record.id; else state.selectedOrderId = record.id;
        renderBusiness();
      });
      host.append(button);
    }
  }

  function renderRecordDetail(host, record, kind) {
    host.replaceChildren();
    if (!record) {
      const empty = document.createElement("div"); empty.className = "detail-empty";
      const mark = document.createElement("span"); mark.className = "detail-empty-mark"; mark.textContent = kind === "note" ? "N" : "O";
      const title = document.createElement("h2"); title.textContent = kind === "note" ? "Select a note" : "Select an order";
      const copy = document.createElement("p"); copy.textContent = "Choose an item from the list to read the full details here.";
      empty.append(mark, title, copy); host.append(empty); return;
    }
    const entry = record.entry;
    const heading = document.createElement("header"); heading.className = "detail-header";
    const headingText = document.createElement("div");
    const eyebrow = document.createElement("span"); eyebrow.className = "eyebrow"; eyebrow.textContent = kind === "note" ? "SHARED NOTE" : "CUSTOMER ORDER";
    const title = document.createElement("h2"); title.textContent = entry.title || "Untitled entry";
    const meta = document.createElement("p"); meta.className = "detail-meta"; meta.textContent = recordLabel(entry) || "Shared workspace";
    headingText.append(eyebrow, title, meta);
    const actions = document.createElement("div"); actions.className = "detail-actions";
    const edit = document.createElement("button"); edit.type = "button"; edit.className = "secondary"; edit.textContent = "Edit"; edit.addEventListener("click", () => openOrder(record));
    const remove = document.createElement("button"); remove.type = "button"; remove.className = "secondary danger-button"; remove.textContent = "Delete"; remove.addEventListener("click", () => deleteOrder(record));
    actions.append(edit, remove); heading.append(headingText, actions); host.append(heading);

    if (entry.kind === "order") {
      const totals = orderTotals(entry); const financials = document.createElement("div"); financials.className = "detail-financials";
      financials.append(detailMetric(formatMoney(totals.subtotal), "Order total"), detailMetric(formatMoney(totals.paid), "Paid"), detailMetric(totals.credit ? formatMoney(totals.credit) : formatMoney(totals.balance), totals.credit ? "Customer credit" : "Balance due", "balance"));
      host.append(financials);
    }
    const content = document.createElement("div"); content.className = "detail-content-scroll";
    if (entry.body) {
      const section = document.createElement("section"); section.className = "detail-section";
      const label = document.createElement("h3"); label.textContent = kind === "note" ? "Note" : "Order notes";
      const body = document.createElement("p"); body.className = "detail-note-text"; body.textContent = entry.body;
      section.append(label, body); content.append(section);
    }
    const rows = entry.checklist || [];
    if (rows.length) {
      const section = document.createElement("section"); section.className = "detail-section";
      const label = document.createElement("h3"); label.textContent = kind === "note" ? "Parts and checklist" : "Order items";
      const list = document.createElement("div"); list.className = "detail-lines";
      for (const row of rows) {
        const line = document.createElement("div"); line.className = "detail-line";
        const main = document.createElement("div"); main.className = "detail-line-main";
        const partName = document.createElement("strong"); partName.textContent = row.text || "Part";
        const partInfo = document.createElement("span"); partInfo.textContent = [row.part_number, row.supplier, row.bin_location].filter(Boolean).join(" | ") || "No part number";
        main.append(partName, partInfo);
        const qty = document.createElement("span"); qty.className = "detail-line-qty"; qty.textContent = "Qty " + (row.quantity || 1);
        line.append(main, qty);
        const cents = Number(row.unit_price_cents) || 0;
        if (entry.kind === "order" && cents) { const price = document.createElement("strong"); price.className = "detail-line-total"; price.textContent = formatMoney(cents * (Number(row.quantity) || 1)); line.append(price); }
        list.append(line);
      }
      section.append(label, list); content.append(section);
    }
    if (!entry.body && !rows.length) { const empty = document.createElement("p"); empty.className = "detail-no-content"; empty.textContent = "No details have been added."; content.append(empty); }
    host.append(content);
  }

  function detailMetric(value, label, kind = "") {
    const card = document.createElement("div"); card.className = "detail-metric" + (kind ? " " + kind : "");
    const amount = document.createElement("strong"); amount.textContent = value;
    const caption = document.createElement("span"); caption.textContent = label;
    card.append(amount, caption); return card;
  }

  function orderTotals(entry) {
    const subtotal = (entry.checklist || []).reduce((sum, row) => sum + (Number(row.quantity) || 1) * (Number(row.unit_price_cents) || 0), 0);
    const paid = Number(entry.payment_received_cents) || 0;
    return {subtotal, paid, balance: Math.max(0, subtotal - paid), credit: Math.max(0, paid - subtotal)};
  }

  function formatMoney(cents) {
    return new Intl.NumberFormat("en-JM", {style:"currency", currency:"JMD", minimumFractionDigits:2, maximumFractionDigits:2}).format((Number(cents) || 0) / 100);
  }

  function summaryCard(value, label) {
    const card = document.createElement("div"); card.className = "summary-card";
    const amount = document.createElement("strong"); amount.textContent = value;
    const caption = document.createElement("span"); caption.textContent = label;
    card.append(amount, caption); return card;
  }

  function openOrder(record = null) {
    const entry = record?.entry || {};
    const kind = entry.kind || "order";
    if (kind === "note") showPanel("notes-panel"); else showPanel("orders-panel");
    $("order-form").reset();
    $("line-error").textContent = "";
    $("entry-kind").value = kind;
    $("order-form-title").textContent = (record ? "Edit " : "New ") + (kind === "note" ? "note" : kind === "list" ? "parts checklist" : "customer order");
    $("order-id").value = record?.id || "";
    $("order-revision").value = record?.revision || "0";
    $("order-title").value = entry.title || "";
    $("order-customer").value = entry.customer || "";
    $("order-ref").value = entry.order_ref || "";
    $("order-status").value = entry.order_status || "new";
    $("order-paid").value = ((Number(entry.payment_received_cents) || 0) / 100).toFixed(2);
    $("order-body").value = entry.body || "";
    state.draftLines = (entry.checklist || []).map(row => ({...row}));
    updateEntryFields();
    renderDraftLines();
    const dialog = $("editor-dialog");
    if (typeof dialog.showModal === "function") dialog.showModal(); else dialog.setAttribute("open", "");
    setTimeout(() => $("line-part").focus(), 0);
  }

  function updateEntryFields() {
    const kind = $("entry-kind").value;
    document.querySelectorAll(".business-order-field").forEach(field => { field.hidden = kind !== "order"; });
    $("add-line").textContent = kind === "order" ? "Add item" : "Add part";
    updateDraftTotals();
  }

  function moneyInputToCents(value) {
    if (String(value).trim() === "") return 0;
    const amount = Number(value);
    return Number.isFinite(amount) && amount >= 0 ? Math.round(amount * 100) : null;
  }

  function updateDraftTotals() {
    const host = $("order-totals"); host.replaceChildren();
    if ($("entry-kind").value !== "order") return;
    const subtotal = state.draftLines.reduce((sum, row) => sum + (Number(row.quantity) || 1) * (Number(row.unit_price_cents) || 0), 0);
    const paid = moneyInputToCents($("order-paid").value) ?? 0;
    const parts = document.createElement("span"); parts.append(document.createTextNode("Order total "));
    const subtotalValue = document.createElement("strong"); subtotalValue.textContent = formatMoney(subtotal); parts.append(subtotalValue);
    const payment = document.createElement("span"); payment.append(document.createTextNode("Paid "));
    const paidValue = document.createElement("strong"); paidValue.textContent = formatMoney(paid); payment.append(paidValue);
    const due = document.createElement("span"); due.className = "balance-total";
    due.append(document.createTextNode(paid > subtotal ? "Credit " : "Balance "));
    const dueValue = document.createElement("strong"); dueValue.textContent = formatMoney(Math.abs(subtotal - paid)); due.append(dueValue);
    host.append(parts, payment, due);
  }

  function renderDraftLines() {
    const host = $("draft-lines"); host.replaceChildren();
    for (const [index, row] of state.draftLines.entries()) {
      const line = document.createElement("div"); line.className = "draft-line";
      const label = document.createElement("span"); label.textContent = (row.quantity || 1) + " x " + row.text + (row.part_number ? " | " + row.part_number : "");
      line.append(label);
      if (Number(row.unit_price_cents) > 0) { const price = document.createElement("span"); price.className = "draft-line-price"; price.textContent = formatMoney((Number(row.quantity) || 1) * row.unit_price_cents); line.append(price); }
      const remove = document.createElement("button"); remove.type = "button"; remove.className = "text-button"; remove.textContent = "Remove";
      remove.addEventListener("click", () => { state.draftLines.splice(index, 1); renderDraftLines(); updateDraftTotals(); });
      line.append(remove); host.append(line);
    }
    updateDraftTotals();
  }

  $("entry-kind").addEventListener("change", () => {
    updateEntryFields();
    const kind = $("entry-kind").value;
    $("order-form-title").textContent = ($("order-id").value ? "Edit " : "New ") + (kind === "note" ? "note" : kind === "list" ? "parts checklist" : "customer order");
  });
  $("order-paid").addEventListener("input", updateDraftTotals);

  $("new-order").addEventListener("click", () => openOrder());
  $("new-note").addEventListener("click", () => openOrder({entry: {kind: "note"}}));
  $("cancel-order").addEventListener("click", () => $("editor-dialog").close?.());
  $("close-editor").addEventListener("click", () => $("editor-dialog").close?.());
  $("notes-search").addEventListener("input", renderBusiness);
  $("orders-search").addEventListener("input", renderBusiness);
  $("add-line").addEventListener("click", () => {
    const kind = $("entry-kind").value;
    const part = $("line-part").value.trim();
    const description = $("line-description").value.trim();
    const quantity = Number($("line-quantity").value);
    const unitPrice = kind === "order" ? moneyInputToCents($("line-price").value) : 0;
    $("line-error").textContent = "";
    if (!description) { $("line-error").textContent = "Add a short description for this part."; $("line-description").focus(); return; }
    if (part && state.draftLines.some(row => String(row.part_number || "").trim().toLocaleLowerCase("en-US") === part.toLocaleLowerCase("en-US"))) {
      $("line-error").textContent = "Number can't be added. This exact part number is already on this note or order."; $("line-part").focus(); return;
    }
    if (!Number.isInteger(quantity) || quantity < 1 || quantity > 9999) { $("line-error").textContent = "Quantity must be between 1 and 9,999."; $("line-quantity").focus(); return; }
    if (unitPrice === null || unitPrice > 1000000000) { $("line-error").textContent = "Enter a valid unit price up to JMD 10,000,000.00."; $("line-price").focus(); return; }
    state.draftLines.push({id: newHexId(), text: description, part_number: part,
      quantity, done: false, unit_price_cents: unitPrice});
    $("line-part").value = ""; $("line-description").value = ""; $("line-quantity").value = "1"; $("line-price").value = "0.00";
    renderDraftLines(); $("line-part").focus();
  });

  $("order-form").addEventListener("submit", async event => {
    event.preventDefault();
    const id = $("order-id").value || newHexId();
    const previous = state.business.find(row => row.id === id)?.entry;
    const now = Date.now() / 1000;
    const kind = $("entry-kind").value;
    const statusValue = $("order-status").value;
    const paid = kind === "order" ? moneyInputToCents($("order-paid").value) : 0;
    if (paid === null || paid > 100000000000000) { $("order-totals").textContent = "Enter a valid payment amount."; $("order-paid").focus(); return; }
    const entry = {...previous, id, kind, title: $("order-title").value.trim(),
      customer: kind === "order" ? $("order-customer").value.trim() : "", order_ref: kind === "order" ? $("order-ref").value.trim() : "",
      order_status: kind === "order" ? statusValue : "new", done: kind === "order" && ["delivered", "cancelled"].includes(statusValue),
      body: $("order-body").value, checklist: state.draftLines, created: previous?.created || now,
      updated: now, written: now, repeat_minutes: 0, next_due: null, priority: previous?.priority || "normal",
      order_type: previous?.order_type || "order", currency: "JMD", payment_received_cents: paid,
      highlights: previous?.highlights || []};
    try {
      const statusHost = kind === "note" ? "notes-status" : "orders-status";
      $(statusHost).textContent = "Saving to Supabase...";
      await request("/api/business/" + id, {method: "PUT", body: {expected_revision: Number($("order-revision").value), entry}});
      if (kind === "note") state.selectedNoteId = id; else state.selectedOrderId = id;
      $("editor-dialog").close?.();
      await loadBusiness();
    } catch (error) { $(kind === "note" ? "notes-status" : "orders-status").textContent = error.message + " Refresh before trying again."; }
  });

  async function deleteOrder(record) {
    if (!confirm(`Delete “${record.entry.title}”?`)) return;
    try {
      await request(`/api/business/${record.id}`, {method: "DELETE", body: {expected_revision: record.revision}});
      await loadBusiness();
    } catch (error) { $("orders-status").textContent = error.message; }
  }

  async function loadInventory() {
    if (!state.token) return;
    const sequence = ++state.inventorySearchSequence;
    const query = $("inventory-search").value.trim();
    $("inventory-status").textContent = "Loading parts…";
    try {
      const params = `query=${encodeURIComponent(query)}&limit=500&offset=0`;
      const [stock, catalog] = await Promise.all([
        request(`/api/inventory?${params}`), request(`/api/catalog?${params}`),
      ]);
      if (sequence !== state.inventorySearchSequence) return;
      if (!Array.isArray(stock.items) || !Array.isArray(catalog.items)) throw new Error("Cannot read the parts catalogue.");
      const bySku = new Map(stock.items.map(item => [String(item.sku || "").toLocaleLowerCase(), item]));
      const merged = [];
      for (const part of catalog.items) {
        const sku = String(part.part_number || "");
        const current = bySku.get(sku.toLocaleLowerCase());
        merged.push({...part, sku, quantity: current?.quantity ?? 0,
          location: current?.location || part.store_location || part.warehouse_location || "",
          supplier: current?.supplier || ""});
        bySku.delete(sku.toLocaleLowerCase());
      }
      merged.push(...bySku.values());
      state.inventory = merged;
      const summary = stock.summary || {};
      $("inventory-summary").replaceChildren();
      for (const [label, value] of [["Catalog parts", catalog.total || catalog.items.length], ["Stocked parts", summary.item_count || stock.items.length], ["Low stock", summary.low_stock_count || 0]]) {
        const card = document.createElement("div"); card.className = "summary-card";
        const number = document.createElement("strong"); number.textContent = Number(value).toLocaleString();
        const caption = document.createElement("span"); caption.textContent = label; card.append(number, caption); $("inventory-summary").append(card);
      }
      renderInventory(); $("inventory-status").textContent = `${catalog.total.toLocaleString()} catalog matches · ${stock.items.length.toLocaleString()} stocked matches`;
    } catch (error) { if (sequence === state.inventorySearchSequence) $("inventory-status").textContent = error.message; }
  }

  function renderInventory() {
    const rows = state.inventory;
    const body = $("inventory-rows"); body.replaceChildren();
    for (const item of rows.slice(0, 1000)) {
      const tr = document.createElement("tr");
      for (const value of [item.sku, item.description, item.quantity, item.location, item.supplier]) {
        const td = document.createElement("td"); td.textContent = value ?? ""; tr.append(td);
      }
      body.append(tr);
    }
    if (rows.length > 1000) $("inventory-status").textContent = `${rows.length.toLocaleString()} matches · showing first 1,000`;
  }
  $("refresh-inventory").addEventListener("click", loadInventory);
  $("inventory-search").addEventListener("input", () => {
    clearTimeout(state.inventorySearchTimer);
    state.inventorySearchTimer = setTimeout(loadInventory, 250);
  });

  async function uploadForPreview(filename, data) {
    const url = apiUrl(`/api/receiving/preview?filename=${encodeURIComponent(filename)}`);
    const response = await fetch(url, {method: "POST", headers: {apikey: SUPABASE_ANON_KEY, Authorization: `Bearer ${state.token}`,
      Accept: "application/json", "Content-Type": "application/octet-stream"}, body: data,
      credentials: "omit", cache: "no-store", redirect: "error"});
    let result; try { result = await response.json(); } catch { throw new Error("The server returned an unreadable response."); }
    if (response.status === 401 && state.token) endSession("Your session has ended. Sign in again.");
    if (!response.ok) throw new Error(result.detail || `Upload failed (${response.status}).`);
    return result;
  }

  $("preview-receiving").addEventListener("click", async () => {
    const file = $("receiving-file").files[0];
    const manual = $("manual-parts").value.trim();
    if (!file && !manual) { $("receiving-status").textContent = "Choose a file or enter part numbers."; return; }
    $("preview-receiving").disabled = true; $("receiving-status").textContent = "Reading upload and matching exact order part numbers…";
    try {
      const result = file ? await uploadForPreview(file.name, file) : await uploadForPreview("received.txt", new Blob([manual]));
      state.receivingMatches = result.matches || [];
      state.receivingPreviewId = result.preview_id || "";
      renderReceivingMatches();
      $("receiving-preview").hidden = false;
      $("receiving-status").textContent = `${result.matched_row_count} matching order rows found in ${result.filename}.`;
      if (!result.matched_row_count) $("receiving-status").textContent = "No exact part number matches were found in open orders.";
    } catch (error) { $("receiving-status").textContent = error.message; }
    finally { $("preview-receiving").disabled = false; }
  });

  function renderReceivingMatches() {
    const host = $("receiving-matches"); host.replaceChildren();
    for (const match of state.receivingMatches) {
      const group = document.createElement("article"); group.className = "record-card";
      const title = document.createElement("h3"); title.textContent = [match.title, match.order_ref, match.customer].filter(Boolean).join(" · "); group.append(title);
      for (const row of match.rows) {
        const label = document.createElement("label"); label.className = "receive-row";
        const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.checked = true;
        checkbox.dataset.entryId = match.entry_id; checkbox.dataset.rowId = row.row_id;
        const text = document.createElement("span"); text.textContent = `${row.quantity} × ${row.description} · ${row.part_number}`;
        label.append(checkbox, text); group.append(label);
      }
      host.append(group);
    }
  }

  $("confirm-receiving").addEventListener("click", async () => {
    const selected = [...$("receiving-matches").querySelectorAll("input[type=checkbox]:checked")];
    if (!selected.length) { $("receiving-status").textContent = "Select at least one matching row."; return; }
    const removals = [];
    for (const match of state.receivingMatches) {
      const rowIds = selected.filter(box => box.dataset.entryId === match.entry_id).map(box => box.dataset.rowId);
      if (rowIds.length) removals.push({entry_id: match.entry_id, expected_revision: match.revision, row_ids: rowIds});
    }
    try {
      $("confirm-receiving").disabled = true;
      const result = await request("/api/receiving/confirm", {method: "POST", body: {
        preview_id: state.receivingPreviewId,
        source: $("receiving-file").files[0]?.name || "Manual received-parts list", removals}});
      $("receiving-status").textContent = `Removed ${result.removed_rows} received part rows from ${result.changed_entries} orders.`;
      $("receiving-preview").hidden = true; $("receiving-file").value = ""; $("manual-parts").value = "";
      await loadBusiness();
    } catch (error) { $("receiving-status").textContent = error.message; }
    finally { $("confirm-receiving").disabled = false; }
  });

  async function downloadBackup(event) {
    event.preventDefault();
    try {
      const response = await fetch(apiUrl("/api/backups/text"), {headers: {apikey: SUPABASE_ANON_KEY, Authorization: `Bearer ${state.token}`},
        credentials: "omit", cache: "no-store", redirect: "error"});
      if (!response.ok) { let data = {}; try { data = await response.json(); } catch {} throw new Error(data.detail || "The server backup is unavailable."); }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a"); link.href = url; link.download = "Jeffery_Auto_Backup.txt"; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { $("backup-location").textContent = error.message; }
  }
  $("download-backup").addEventListener("click", downloadBackup);
  document.querySelectorAll(".workspace-tab").forEach(button => button.addEventListener("click", () => showPanel(button.dataset.panel)));

  $("team-room").addEventListener("click", () => selectConversation(null));
  $("people-search").addEventListener("input", renderUsers);
  $("refresh-users").addEventListener("click", fetchUsers);
  $("refresh-messages").addEventListener("click", () => fetchMessages(state.cursor === 0));
  $("signout").addEventListener("click", async () => {
    const token = state.token;
    endSession();
    try { await request("/api/auth/logout", { method: "POST", token }); }
    catch { $("auth-error").textContent = "Signed out here. The server could not be reached to revoke the session; it expires automatically."; }
  });
  $("coworker-form").addEventListener("submit", async event => {
    event.preventDefault();
    const username = $("coworker-username").value.trim();
    const password = $("coworker-password").value;
    $("coworker-status").textContent = "Creating account…";
    try {
      await request("/api/admin/users", {method: "POST", body: {username, password}});
      $("coworker-password").value = "";
      $("coworker-username").value = "";
      $("coworker-status").textContent = `Created ${username}'s username and password account.`;
      await fetchUsers();
    } catch (error) { $("coworker-status").textContent = error.message; }
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && state.token) { fetchUsers(); fetchMessages(state.cursor === 0); }
    else clearTimeout(state.pollTimer);
  });
})();
