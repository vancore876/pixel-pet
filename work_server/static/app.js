"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const state = {
    token: "", user: null, users: [], peer: null, cursor: 0, messages: new Map(),
    drafts: new Map(), authEpoch: 0, conversationEpoch: 0, requests: new Set(),
    pollTimer: null, usersTimer: null, pollBusy: null, sending: false, signingUp: false,
  };
  const conversationKey = (peer = state.peer) => peer === null ? "team" : String(peer);
  $("server-name").textContent = location.host;

  function status(text, error = false) {
    $("connection-status").textContent = text;
    $("connection-status").classList.toggle("error-status", error);
  }

  async function request(path, { method = "GET", body, token = state.token } = {}) {
    const controller = new AbortController();
    state.requests.add(controller);
    const timer = setTimeout(() => controller.abort(), 10000);
    try {
      const headers = { Accept: "application/json" };
      if (body !== undefined) headers["Content-Type"] = "application/json";
      if (token) headers.Authorization = `Bearer ${token}`;
      const response = await fetch(path, {
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
      if (error instanceof TypeError) throw new Error("Cannot reach the work server. Check your office connection.");
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
    $("auth-subtitle").textContent = signingUp ? "Create your account on this work server." : "Sign in to join your coworkers.";
    $("auth-submit").textContent = signingUp ? "Create account" : "Sign in";
    $("auth-error").textContent = "";
  }

  function endSession(message = "") {
    state.authEpoch++;
    state.conversationEpoch++;
    clearTimeout(state.pollTimer);
    clearInterval(state.usersTimer);
    for (const controller of state.requests) controller.abort();
    state.token = "";
    state.user = null;
    state.users = [];
    state.messages.clear();
    state.drafts.clear();
    state.pollBusy = null;
    state.sending = false;
    $("password").value = "";
    $("confirm-password").value = "";
    $("message-body").value = "";
    $("messages").replaceChildren();
    $("people-list").replaceChildren();
    $("send-message").disabled = false;
    $("chat-view").hidden = true;
    $("auth-view").hidden = false;
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
      const data = await request(`/api/auth/${state.signingUp ? "register" : "login"}`, {
        method: "POST", token: "", body: { username: $("username").value.trim(), password: $("password").value },
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
      $("account-avatar").textContent = data.user.username.slice(0, 1).toUpperCase();
      $("auth-view").hidden = true;
      $("chat-view").hidden = false;
      selectConversation(null);
      await fetchUsers();
      if (epoch === state.authEpoch) state.usersTimer = setInterval(fetchUsers, 15000);
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
        state.pollTimer = setTimeout(() => fetchMessages(state.cursor === 0), 2000);
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
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && state.token) { fetchUsers(); fetchMessages(state.cursor === 0); }
    else clearTimeout(state.pollTimer);
  });
})();
