"use strict";

(() => {
  const pageStartedAt = performance.now();
  const SUPABASE_FUNCTION_URL = window.JEFFERY_SUPABASE_FUNCTION_URL;
  const SUPABASE_REGISTER_URL = window.JEFFERY_SUPABASE_REGISTER_URL;
  const SUPABASE_ANON_KEY = window.JEFFERY_SUPABASE_ANON_KEY;
  const AUTOMATE_FUNCTION_URL = window.JEFFERY_AUTOMATE_SUPABASE_FUNCTION_URL;
  const AUTOMATE_ANON_KEY = window.JEFFERY_AUTOMATE_SUPABASE_ANON_KEY;
  if (typeof SUPABASE_FUNCTION_URL !== "string" || typeof SUPABASE_REGISTER_URL !== "string" || typeof SUPABASE_ANON_KEY !== "string") throw new Error("Jeffery cloud configuration is missing.");
  const apiUrl = path => SUPABASE_FUNCTION_URL + path.replace(/^\/api(?=\/|\?|$)/, "");
  const $ = (id) => document.getElementById(id);
  const state = {
    token: "", user: null, users: [], peer: null, cursor: 0, messages: new Map(),
    drafts: new Map(), authEpoch: 0, conversationEpoch: 0, requests: new Set(),
    pollTimer: null, notificationTimer: null, notificationBusy: false, directCursors: new Map(), announcedMessages: new Set(),
    usersTimer: null, dataTimer: null, pollBusy: null, sending: false, signingUp: false,
    business: [], businessCursor: 0, businessLoading: false, selectedNoteId: "", selectedOrderId: "", inventory: [], noteParts: null, inventorySearchSequence: 0, inventorySearchTimer: null, draftLines: [], receivingMatches: [], receivingPreviewId: "", automateBusy: false,
  };
  let messageAudio = null;
  const themeKey = "jeffery-theme";
  const savedTheme = localStorage.getItem(themeKey);
  if (savedTheme === "dark" || savedTheme === "light") document.documentElement.dataset.theme = savedTheme;
  $("theme").addEventListener("click", () => {
    const nextTheme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = nextTheme;
    localStorage.setItem(themeKey, nextTheme);
  });
  const rotorHoles = $("rotor-holes");
  for (let index = 0; index < 12; index++) {
    const angle = index * Math.PI / 6;
    const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    dot.setAttribute("cx", String(100 + 46 * Math.cos(angle)));
    dot.setAttribute("cy", String(100 + 46 * Math.sin(angle)));
    dot.setAttribute("r", "5");
    rotorHoles.append(dot);
  }
  const conversationKey = (peer = state.peer) => peer === null ? "team" : String(peer);
  const newHexId = () => {
    if (window.crypto?.randomUUID) return crypto.randomUUID().replaceAll("-", "");
    return Array.from({length: 32}, () => Math.floor(Math.random() * 16).toString(16)).join("");
  };
  $("server-name").textContent = location.host;

  function unlockMessageAudio() {
    const AudioContextType = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextType) return;
    try {
      messageAudio ||= new AudioContextType();
      if (messageAudio.state === "suspended") void messageAudio.resume().catch(() => {});
    } catch {}
  }

  function playMessageSound() {
    if (!messageAudio || messageAudio.state !== "running") return;
    try {
      const now = messageAudio.currentTime;
      for (const [offset, frequency] of [[0, 740], [0.12, 990]]) {
        const oscillator = messageAudio.createOscillator();
        const gain = messageAudio.createGain();
        oscillator.type = "sine";
        oscillator.frequency.value = frequency;
        gain.gain.setValueAtTime(0.0001, now + offset);
        gain.gain.exponentialRampToValueAtTime(0.12, now + offset + 0.015);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + offset + 0.18);
        oscillator.connect(gain); gain.connect(messageAudio.destination);
        oscillator.start(now + offset); oscillator.stop(now + offset + 0.19);
      }
    } catch {}
  }

  function refreshNotificationButton() {
    const button = $("enable-notifications");
    if (!("Notification" in window)) { button.hidden = true; return; }
    button.hidden = false;
    button.textContent = Notification.permission === "granted" ? "Notifications enabled" :
      Notification.permission === "denied" ? "Notifications blocked in Chrome" : "Enable notifications";
    button.title = Notification.permission === "denied"
      ? "Chrome has blocked this site's prompt. Use the site controls beside the address bar, set Notifications to Allow, then reload."
      : "Allow desktop alerts for new messages when Jeffery is not open.";
    button.disabled = Notification.permission === "granted";
  }

  function requestNotificationPermission() {
    if (!("Notification" in window) || Notification.permission !== "default") {
      refreshNotificationButton();
      return;
    }
    try {
      // Call this directly from the sign-in or button gesture so Chrome can show its prompt.
      void Notification.requestPermission().then(refreshNotificationButton).catch(refreshNotificationButton);
    } catch {
      refreshNotificationButton();
    }
  }

  function showMessageToast(message) {
    const toast = document.createElement("button");
    toast.type = "button"; toast.className = "message-alert";
    const title = document.createElement("strong");
    title.textContent = message.sender_username + (message.recipient_id == null ? " · Team Room" : " sent you a message");
    const body = document.createElement("span"); body.textContent = message.body.slice(0, 180);
    toast.append(title, body);
    toast.addEventListener("click", () => {
      showPanel("chat-panel");
      selectConversation(message.recipient_id == null ? null : message.sender_id);
      toast.remove();
    });
    $("message-alerts").append(toast);
    setTimeout(() => toast.remove(), 9000);
  }

  function notifyIncomingMessage(message) {
    if (!state.user || message.sender_id === state.user.id || state.announcedMessages.has(message.id)) return;
    state.announcedMessages.add(message.id);
    if (state.announcedMessages.size > 500) state.announcedMessages.delete(state.announcedMessages.values().next().value);
    playMessageSound();
    const peer = message.recipient_id == null ? null : message.sender_id;
    const isOpenConversation = !document.hidden && !$("chat-panel").hidden && state.peer === peer;
    if (isOpenConversation) return;
    showMessageToast(message);
    if ("Notification" in window && Notification.permission === "granted") {
      try {
        const notice = new Notification(message.recipient_id == null ? "New Team Room message" : `Message from ${message.sender_username}`, {
          body: message.body.slice(0, 220), tag: `jeffery-message-${message.id}`,
        });
        notice.onclick = () => {
          window.focus(); showPanel("chat-panel"); selectConversation(peer); notice.close();
        };
      } catch {}
    }
  }

  $("enable-notifications").addEventListener("click", () => {
    unlockMessageAudio();
    requestNotificationPermission();
  });
  refreshNotificationButton();

  function status(text, error = false) {
    $("connection-status").textContent = text;
    $("connection-status").classList.toggle("error-status", error);
  }

  async function request(path, { method = "GET", body, token = state.token, endpoint = SUPABASE_FUNCTION_URL, apiKey = SUPABASE_ANON_KEY } = {}) {
    const controller = new AbortController();
    state.requests.add(controller);
    const timer = setTimeout(() => controller.abort(), 10000);
    try {
      const headers = { Accept: "application/json" };
      if (body !== undefined) headers["Content-Type"] = "application/json";
      headers.apikey = apiKey;
      headers.Authorization = `Bearer ${token || apiKey}`;
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
    clearTimeout(state.notificationTimer);
    clearInterval(state.usersTimer);
    clearInterval(state.dataTimer);
    for (const controller of state.requests) controller.abort();
    state.token = "";
    state.user = null;
    state.users = [];
    state.messages.clear();
    state.drafts.clear();
    state.pollBusy = null;
    state.notificationBusy = false; state.directCursors.clear(); state.announcedMessages.clear();
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
    unlockMessageAudio();
    if (state.signingUp && $("password").value !== $("confirm-password").value) {
      $("auth-error").textContent = "The passwords do not match.";
      return;
    }
    if (!state.signingUp) requestNotificationPermission();
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
      state.directCursors.clear(); state.announcedMessages.clear();
      $("password").value = "";
      $("confirm-password").value = "";
      $("account-name").textContent = data.user.username;
      $("web-account-name").textContent = data.user.username;
      $("account-avatar").textContent = data.user.username.slice(0, 1).toUpperCase();
      $("chat-account-name").textContent = data.user.username;
      $("manage-coworkers").hidden = data.user.role !== "admin";
      $("auth-view").hidden = true;
      $("chat-view").hidden = false;
      const requestedPanel = new URLSearchParams(window.location.search).get("panel");
      const requestedViews = {notes: "notes-panel", orders: "orders-panel", chat: "chat-panel"};
      showPanel(requestedViews[requestedPanel] || "home-panel");
      selectConversation(null);
      await fetchUsers();
      void fetchNotifications();
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

  function acceptMessages(messages, notify = false) {
    if (!Array.isArray(messages)) throw new Error("The server returned an invalid message list.");
    let changed = false;
    for (const message of messages) {
      if (!Number.isInteger(message.id) || typeof message.body !== "string" || typeof message.sender_username !== "string") {
        throw new Error("The server returned an invalid message.");
      }
      if (!state.messages.has(message.id)) {
        state.messages.set(message.id, message);
        if (notify) notifyIncomingMessage(message);
        changed = true;
      }
    }
    if (changed) renderMessages();
  }

  async function fetchMessages(initial = false) {
    if (!state.token || document.hidden) return;
    const notifyNew = !initial;
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
        acceptMessages(data.messages, notifyNew);
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

  async function pollInboxConversation(key, path) {
    const epoch = state.authEpoch;
    const cursor = state.directCursors.get(key) || { id: 0, initialized: false };
    const data = await request(path + (path.includes("?") ? "&" : "?") + `limit=100${cursor.id ? `&after_id=${cursor.id}` : ""}`);
    if (epoch !== state.authEpoch || !state.token) return;
    if (!Array.isArray(data.messages)) throw new Error("Could not check incoming messages.");
    let lastId = cursor.id;
    for (const message of data.messages) {
      if (!Number.isSafeInteger(message.id) || typeof message.body !== "string" || typeof message.sender_username !== "string") {
        throw new Error("An incoming message could not be read.");
      }
      lastId = Math.max(lastId, message.id);
      if (cursor.initialized) notifyIncomingMessage(message);
    }
    state.directCursors.set(key, { id: lastId, initialized: cursor.initialized || data.has_more !== true });
  }

  async function fetchNotifications() {
    if (!state.token || state.notificationBusy) return;
    state.notificationBusy = true;
    const epoch = state.authEpoch;
    try {
      const activeChat = !document.hidden && !$("chat-panel").hidden;
      if (!activeChat || state.peer !== null) await pollInboxConversation("team", "/api/messages");
      const peers = state.users.filter(user => user.id !== state.user.id && (!activeChat || state.peer !== user.id));
      const chunkSize = 5;
      for (let offset = 0; offset < peers.length; offset += chunkSize) {
        const batch = peers.slice(offset, offset + chunkSize);
        await Promise.all(batch.map(user => pollInboxConversation(user.id, `/api/messages?peer_id=${user.id}`)));
      }
    } catch (error) {
      if (epoch === state.authEpoch && state.token) status(error.message, true);
    } finally {
      state.notificationBusy = false;
      if (epoch === state.authEpoch && state.token) state.notificationTimer = setTimeout(fetchNotifications, 5000);
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
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); $("message-form").requestSubmit(); }
  });

  function showPanel(id) {
    for (const panel of ["home-panel", "chat-panel", "coworkers-panel", "notes-panel", "orders-panel", "inventory-panel", "receiving-panel", "automate-panel", "backup-panel"]) {
      $(panel).hidden = panel !== id;
    }
    $("home-panel").hidden = id !== "home-panel";
    $("app-main").scrollTop = 0;
    document.querySelectorAll(".workspace-tab").forEach(button => {
      const selected = button.dataset.panel === id;
      button.classList.toggle("selected", selected);
      button.setAttribute("aria-pressed", String(selected));
    });
    if (id === "notes-panel" || id === "orders-panel") loadBusiness();
    if (id === "inventory-panel") loadInventory();
  }

  async function queueAutoMateRead(operation, payload) {
    if (state.automateBusy) return;
    state.automateBusy = true;
    const statusNode = $("automate-status");
    const resultNode = $("automate-result");
    const buttons = [...$("automate-panel").querySelectorAll("button[type=submit]")];
    buttons.forEach(button => { button.disabled = true; });
    resultNode.hidden = true;
    resultNode.textContent = "";
    try {
      statusNode.textContent = "Sending a secure lookup request to the AutoMate bridge…";
      const created = await request("/jobs", {method: "POST", body: {operation, payload}, endpoint: AUTOMATE_FUNCTION_URL, apiKey: AUTOMATE_ANON_KEY});
      const jobId = created?.job?.id;
      if (typeof jobId !== "string") throw new Error("The bridge did not accept this lookup.");
      const deadline = Date.now() + 120000;
      while (Date.now() < deadline) {
        await new Promise(resolve => setTimeout(resolve, 1500));
        const response = await request(`/jobs/${encodeURIComponent(jobId)}`, {endpoint: AUTOMATE_FUNCTION_URL, apiKey: AUTOMATE_ANON_KEY});
        const job = response?.job;
        if (!job) throw new Error("The lookup request could not be found.");
        if (job.status === "completed") {
          resultNode.textContent = JSON.stringify(job.result ?? [], null, 2);
          resultNode.hidden = false;
          statusNode.textContent = "AutoMate lookup completed.";
          return;
        }
        if (job.status === "failed" || job.status === "expired") throw new Error(`AutoMate could not complete this lookup (${job.error_code || job.status}).`);
        statusNode.textContent = job.status === "claimed" ? "The local bridge is reading AutoMate…" : "Lookup queued. Waiting for the local bridge…";
      }
      statusNode.textContent = "Lookup is still queued. Start or check the local bridge, then submit the lookup again.";
    } catch (error) {
      statusNode.textContent = error.message || "Could not connect to the AutoMate bridge.";
    } finally {
      state.automateBusy = false;
      buttons.forEach(button => { button.disabled = false; });
    }
  }

  $("automate-part-form").addEventListener("submit", event => {
    event.preventDefault();
    const part = $("automate-part-number").value.trim();
    if (part) queueAutoMateRead("get_inventory_item", {part_number: part});
  });
  $("automate-order-form").addEventListener("submit", event => {
    event.preventDefault();
    const number = Number($("automate-order-number").value);
    if (Number.isSafeInteger(number) && number > 0) queueAutoMateRead("get_order", {transaction_number: number});
  });

  const searchText = value => String(value || "").toLocaleLowerCase("en-US");

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

    renderNoteCards($("notes-list"), noteRecords);
    renderOrderBoard($("orders-board"), orderRecords);
    const homeOpen = orders.filter(({entry}) => entry.kind === "order" && !entry.done && !["delivered", "cancelled"].includes(entry.order_status) && entry.order_type !== "quote");
    $("home-open-count").textContent = String(homeOpen.length);
    $("home-ready-count").textContent = String(homeOpen.filter(({entry}) => entry.order_status === "ready").length);
    $("home-balance").textContent = formatMoney(homeOpen.reduce((sum, {entry}) => sum + orderTotals(entry).balance, 0));
  }

  function renderNoteCards(host, records) {
    host.replaceChildren();
    if (!records.length) {
      const empty = document.createElement("p"); empty.className = "empty";
      empty.textContent = $("notes-search").value.trim() ? "No notes match that search." : "No team notes yet. Add one to get started.";
      host.append(empty); return;
    }
    for (const record of records) {
      const entry = record.entry;
      const card = document.createElement("article"); card.className = "note";
      if (entry.pinned) card.classList.add("pin");
      const meta = document.createElement("small"); meta.textContent = entry.pinned ? "Pinned note" : "Shared with the team";
      const title = document.createElement("h2"); title.textContent = entry.title || "Untitled note";
      const content = document.createElement("div"); content.className = "note-content-scroll";
      if (entry.body) { const body = document.createElement("p"); body.textContent = entry.body; content.append(body); }
      if ((entry.checklist || []).length) {
        const lines = document.createElement("div"); lines.className = "note-part-list";
        for (const row of entry.checklist) {
          const line = document.createElement("div"); line.className = "note-part-row";
          const part = document.createElement("span"); part.textContent = [row.text, row.part_number].filter(Boolean).join(" · ") || "Part";
          const quantity = document.createElement("b"); quantity.textContent = `Qty ${row.quantity || 1}`;
          line.append(part, quantity); lines.append(line);
        }
        content.append(lines);
      }
      if (!entry.body && !(entry.checklist || []).length) { const emptyText = document.createElement("p"); emptyText.className = "muted"; emptyText.textContent = "No details yet."; content.append(emptyText); }
      const actions = document.createElement("div"); actions.className = "note-actions";
      const jump = document.createElement("button"); jump.type = "button"; jump.className = "note-link"; jump.textContent = "↓ Jump to bottom"; jump.addEventListener("click", () => content.scrollTo({top: content.scrollHeight, behavior: "smooth"}));
      const edit = document.createElement("button"); edit.type = "button"; edit.className = "note-link"; edit.textContent = "Edit"; edit.addEventListener("click", () => openOrder(record));
      const remove = document.createElement("button"); remove.type = "button"; remove.className = "note-link danger-link"; remove.textContent = "Delete"; remove.addEventListener("click", () => deleteOrder(record));
      actions.append(jump, edit, remove);
      card.append(meta, title, content, actions); host.append(card);
    }
  }

  function renderOrderBoard(host, records) {
    host.replaceChildren();
    const statuses = [["new", "New"], ["preparing", "Preparing"], ["ready", "Ready"], ["delivered", "Delivered"], ["cancelled", "Cancelled"]];
    const selectedStage = $("orders-stage-filter")?.value || "all";
    host.classList.toggle("filtered-status", selectedStage !== "all");
    for (const [status, labelText] of statuses) {
      if (selectedStage !== "all" && selectedStage !== status) continue;
      const matching = records.filter(({entry}) => (entry.order_status || "new").toLowerCase() === status &&
        (!$("orders-search").value.trim() || searchText([entry.title, entry.customer, entry.order_ref, entry.body, ...(entry.checklist || []).flatMap(row => [row.text, row.part_number])].join(" ")).includes(searchText($("orders-search").value.trim()))));
      const column = document.createElement("section"); column.className = "col"; column.dataset.status = status;
      const heading = document.createElement("h2"); heading.textContent = labelText;
      const count = document.createElement("b"); count.className = "column-count"; count.textContent = String(matching.length); heading.append(count); column.append(heading);
      if (!matching.length) { const empty = document.createElement("p"); empty.className = "empty"; empty.textContent = "Nothing here yet."; column.append(empty); }
      for (const record of matching) {
        const entry = record.entry; const card = document.createElement("article"); card.className = "order-card";
        const type = document.createElement("span"); type.className = "order-type"; type.textContent = entry.kind === "list" ? "Parts checklist" : "Customer order";
        const title = document.createElement("h3"); title.textContent = entry.title || "Untitled order";
        const customer = document.createElement("p"); customer.className = "order-card-meta"; customer.textContent = [entry.customer, entry.order_ref].filter(Boolean).join(" · ") || "No customer reference";
        const parts = document.createElement("p"); parts.className = "order-card-meta"; parts.textContent = `${(entry.checklist || []).length} parts · Paid ${formatMoney(Number(entry.payment_received_cents) || 0)}`;
        const totals = orderTotals(entry); const foot = document.createElement("div"); foot.className = "order-card-foot";
        const due = document.createElement("strong"); due.textContent = totals.credit ? `Credit ${formatMoney(totals.credit)}` : `Due ${formatMoney(totals.balance)}`;
        const statusSelect = document.createElement("select"); statusSelect.setAttribute("aria-label", `Status for ${entry.title || "order"}`);
        for (const [value, label] of statuses) { const option = document.createElement("option"); option.value = value; option.textContent = label; option.selected = value === status; statusSelect.append(option); }
        statusSelect.addEventListener("change", () => updateOrderStatus(record, statusSelect.value));
        foot.append(due, statusSelect);
        const actions = document.createElement("div"); actions.className = "order-card-actions";
        const edit = document.createElement("button"); edit.type = "button"; edit.className = "note-link"; edit.textContent = "Edit"; edit.addEventListener("click", () => openOrder(record));
        const remove = document.createElement("button"); remove.type = "button"; remove.className = "note-link danger-link"; remove.textContent = "Delete"; remove.addEventListener("click", () => deleteOrder(record));
        actions.append(edit, remove); card.append(type, title, customer, parts, foot, actions); column.append(card);
      }
      host.append(column);
    }
    if (!records.length && $("orders-search").value.trim()) { const empty = document.createElement("p"); empty.className = "empty"; empty.textContent = "No orders match that search."; host.append(empty); }
  }

  async function updateOrderStatus(record, orderStatus) {
    const entry = {...record.entry, order_status: orderStatus, done: ["delivered", "cancelled"].includes(orderStatus), updated: Date.now() / 1000, written: Date.now() / 1000};
    try {
      $("orders-status").textContent = "Updating order…";
      await request(`/api/business/${record.id}`, {method: "PUT", body: {expected_revision: record.revision, entry}});
      await loadBusiness();
    } catch (error) { $("orders-status").textContent = error.message; await loadBusiness(); }
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
    const jumpBottom = document.createElement("button"); jumpBottom.type = "button"; jumpBottom.className = "secondary jump-bottom-button"; jumpBottom.textContent = "↓ Bottom"; jumpBottom.title = "Jump to the bottom of this note or list"; jumpBottom.setAttribute("aria-label", "Jump to the bottom of this note or list"); jumpBottom.addEventListener("click", () => host.scrollTo({top: host.scrollHeight, behavior: "smooth"}));
    const edit = document.createElement("button"); edit.type = "button"; edit.className = "secondary"; edit.textContent = "Edit"; edit.addEventListener("click", () => openOrder(record));
    const remove = document.createElement("button"); remove.type = "button"; remove.className = "secondary danger-button"; remove.textContent = "Delete"; remove.addEventListener("click", () => deleteOrder(record));
    actions.append(jumpBottom, edit, remove); heading.append(headingText, actions); host.append(heading);

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
    $("editor-dialog").classList.toggle("note-editor", kind === "note");
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
    if (kind === "note") void populateNotePartOptions();
    const editor = $("editor-dialog");
    if (!editor.open) editor.showModal();
    setTimeout(() => $("line-part").focus(), 0);
  }

  function closeOrderEditor() {
    const editor = $("editor-dialog");
    if (editor.open) editor.close();
    renderBusiness();
  }

  function updateEntryFields() {
    const kind = $("entry-kind").value;
    document.querySelectorAll(".business-order-field").forEach(field => { field.hidden = kind !== "order"; });
    document.querySelectorAll(".note-catalog-field").forEach(field => { field.hidden = kind !== "note"; });
    $("editor-dialog").classList.toggle("note-editor", kind === "note");
    $("add-line").textContent = kind === "order" ? "Add item" : "Add part";
    updateDraftTotals();
  }

  async function populateNotePartOptions() {
    const select = $("note-part-select");
    select.replaceChildren(new Option("Loading parts…", ""));
    select.disabled = true;
    try {
      if (!state.noteParts) {
        const catalog = await request("/api/catalog?limit=500&offset=0");
        if (!Array.isArray(catalog.items)) throw new Error("Cannot read the parts catalogue.");
        state.noteParts = catalog.items.map(item => ({...item, sku: item.part_number || item.sku || ""}));
      }
      select.replaceChildren(new Option("Select a catalog part…", ""));
      for (const item of state.noteParts) {
        const partNumber = String(item.sku || item.part_number || "").trim();
        if (!partNumber) continue;
        select.add(new Option([partNumber, item.description].filter(Boolean).join(" — "), partNumber));
      }
      if (!select.options.length || select.options.length === 1) select.add(new Option("No catalog parts found", ""));
    } catch (error) {
      select.replaceChildren(new Option("Parts catalogue unavailable", ""));
      $("line-error").textContent = error.message;
    } finally { select.disabled = false; }
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
      const fields = document.createElement("div"); fields.className = "draft-line-fields";
      const makeLineInput = (labelText, field, value, type = "text") => {
        const input = document.createElement("input"); input.type = type; input.value = value ?? ""; input.setAttribute("aria-label", labelText);
        if (type === "number") { input.min = "1"; input.max = "9999"; input.step = "1"; }
        input.addEventListener("change", () => {
          if (field === "quantity") {
            const quantity = Number(input.value);
            if (!Number.isInteger(quantity) || quantity < 1 || quantity > 9999) { input.value = String(row.quantity || 1); input.focus(); return; }
            state.draftLines[index].quantity = quantity;
          } else state.draftLines[index][field] = input.value.trim();
          updateDraftTotals();
        });
        return input;
      };
      fields.append(makeLineInput("Part description", "text", row.text), makeLineInput("Part number", "part_number", row.part_number), makeLineInput("Quantity", "quantity", row.quantity || 1, "number"));
      if ($("entry-kind").value === "order") {
        const price = document.createElement("input"); price.type = "number"; price.min = "0"; price.max = "10000000"; price.step = "0.01"; price.inputMode = "decimal";
        price.value = ((Number(row.unit_price_cents) || 0) / 100).toFixed(2); price.setAttribute("aria-label", "Unit price in JMD");
        price.addEventListener("change", () => { const cents = moneyInputToCents(price.value); if (cents === null || cents > 1000000000) { price.value = ((Number(row.unit_price_cents) || 0) / 100).toFixed(2); price.focus(); return; } state.draftLines[index].unit_price_cents = cents; updateDraftTotals(); });
        fields.append(price);
      }
      line.append(fields);
      const remove = document.createElement("button"); remove.type = "button"; remove.className = "text-button"; remove.textContent = "Remove";
      remove.addEventListener("click", () => { state.draftLines.splice(index, 1); renderDraftLines(); updateDraftTotals(); });
      line.append(remove); host.append(line);
    }
    updateDraftTotals();
  }

  $("entry-kind").addEventListener("change", () => {
    updateEntryFields();
    const kind = $("entry-kind").value;
    if (kind === "note") void populateNotePartOptions();
    $("order-form-title").textContent = ($("order-id").value ? "Edit " : "New ") + (kind === "note" ? "note" : kind === "list" ? "parts checklist" : "customer order");
  });
  $("order-paid").addEventListener("input", updateDraftTotals);

  $("new-order").addEventListener("click", () => openOrder());
  $("new-note").addEventListener("click", () => openOrder({entry: {kind: "note"}}));
  $("cancel-order").addEventListener("click", closeOrderEditor);
  $("close-editor").addEventListener("click", closeOrderEditor);
  $("editor-dialog").addEventListener("close", renderBusiness);
  $("notes-search").addEventListener("input", renderBusiness);
  $("orders-search").addEventListener("input", renderBusiness);
  $("orders-stage-filter").addEventListener("change", renderBusiness);
  $("note-part-select").addEventListener("change", () => {
    const partNumber = $("note-part-select").value;
    if (!partNumber) return;
    const item = (state.noteParts || []).find(row => String(row.sku || row.part_number || "").trim() === partNumber);
    $("line-part").value = partNumber;
    if (item?.description) $("line-description").value = item.description;
  });
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
    $("line-part").value = ""; $("line-description").value = ""; $("line-quantity").value = "1"; $("line-price").value = "0.00"; $("note-part-select").value = "";
    renderDraftLines(); $("line-part").focus();
  });

  $("order-form").addEventListener("submit", async event => {
    event.preventDefault();
    const id = $("order-id").value || newHexId();
    const previous = state.business.find(row => row.id === id)?.entry;
    const now = Date.now() / 1000;
    const kind = $("entry-kind").value;
    if (state.draftLines.some(row => !String(row.text || "").trim() || !Number.isInteger(Number(row.quantity)) || Number(row.quantity) < 1 || Number(row.quantity) > 9999)) {
      $("line-error").textContent = "Each item needs a description and a quantity from 1 to 9,999."; return;
    }
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
      closeOrderEditor();
      await loadBusiness();
    } catch (error) { $(kind === "note" ? "notes-status" : "orders-status").textContent = error.message + " Refresh before trying again."; }
  });

  async function deleteOrder(record) {
    if (!confirm(`Delete “${record.entry.title}”?`)) return;
    try {
      await request(`/api/business/${record.id}`, {method: "DELETE", body: {expected_revision: record.revision}});
      await loadBusiness();
    } catch (error) { $(record.entry.kind === "note" ? "notes-status" : "orders-status").textContent = error.message; }
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
    $("preview-receiving").disabled = true; $("receiving-status").textContent = "Reading upload and matching exact part numbers in notes, checklists, and open orders…";
    try {
      const result = file ? await uploadForPreview(file.name, file) : await uploadForPreview("received.txt", new Blob([manual]));
      state.receivingMatches = result.matches || [];
      state.receivingPreviewId = result.preview_id || "";
      renderReceivingMatches();
      $("receiving-preview").hidden = false;
      $("receiving-status").textContent = `${result.matched_row_count} matching rows found in notes, checklists, and open orders in ${result.filename}. Review them below before removing.`;
      if (!result.matched_row_count) $("receiving-status").textContent = "No exact part number matches were found in notes, checklists, or open orders.";
    } catch (error) { $("receiving-status").textContent = error.message; }
    finally { $("preview-receiving").disabled = false; }
  });

  function renderReceivingMatches() {
    const host = $("receiving-matches"); host.replaceChildren();
    for (const match of state.receivingMatches) {
      const group = document.createElement("article"); group.className = "record-card";
      const kind = document.createElement("span"); kind.className = "receive-kind";
      kind.textContent = match.kind === "note" ? "Shared note" : match.kind === "list" ? "Parts checklist" : "Customer order";
      const title = document.createElement("h3"); title.textContent = [match.title, match.order_ref, match.customer].filter(Boolean).join(" · "); group.append(kind, title);
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
      const kinds = (result.entries || []).reduce((counts, entry) => { counts[entry.kind || "order"] = (counts[entry.kind || "order"] || 0) + 1; return counts; }, {});
      const places = [["note", "notes"], ["list", "checklists"], ["order", "orders"]].filter(([kind]) => kinds[kind]).map(([kind, label]) => `${kinds[kind]} ${label}`);
      $("receiving-status").textContent = `Removed ${result.removed_rows} received part rows from ${places.join(", ") || `${result.changed_entries} entries`}.`;
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
  document.querySelectorAll(".workspace-tab, .shortcut-card").forEach(button => button.addEventListener("click", () => showPanel(button.dataset.panel)));

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
    if (!document.hidden && state.token) { fetchUsers(); fetchMessages(state.cursor === 0); fetchNotifications(); }
    else clearTimeout(state.pollTimer);
  });
  const bootStages = [
    [350, "Calibrating Jeffery's workspace", "SYSTEM STARTUP · 01"],
    [1150, "Mapping chat, notes and order tools", "WORKSPACE MAP · 02"],
    [2000, "Preparing parts and receiving desk", "PARTS NETWORK · 03"],
    [2800, "Bringing your workday into focus", "READY SEQUENCE · 04"],
  ];
  for (const [at, message, step] of bootStages) {
    window.setTimeout(() => {
      const statusLine = $("loading-status");
      const stepLine = $("loading-step");
      if (statusLine) statusLine.textContent = message;
      if (stepLine) stepLine.textContent = step;
    }, Math.max(0, at - (performance.now() - pageStartedAt)));
  }
  window.setTimeout(() => {
    const loader = $("loading-screen");
    if (!loader) return;
    loader.classList.add("is-hidden");
    window.setTimeout(() => loader.remove(), 650);
  }, Math.max(0, 3600 - (performance.now() - pageStartedAt)));
})();
