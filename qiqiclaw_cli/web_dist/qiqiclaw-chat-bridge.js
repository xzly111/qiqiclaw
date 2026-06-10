(function () {
  "use strict";

  var activeChatWs = null;
  var pendingPrompt = null;
  var pendingDropSubmits = {};
  var OriginalWebSocket = window.WebSocket;

  function isChatSocket(url) {
    return String(url || "").indexOf("/api/ws") !== -1;
  }

  function rpc(method, params) {
    if (!activeChatWs || activeChatWs.readyState !== 1) return;
    activeChatWs.send(JSON.stringify({
      jsonrpc: "2.0",
      id: Date.now() + Math.floor(Math.random() * 1000),
      method: method,
      params: params || {}
    }));
  }

  function sendRaw(ws, data) {
    return OriginalWebSocket.prototype.send.call(ws, data);
  }

  function nextRpcId() {
    return Date.now() + Math.floor(Math.random() * 1000);
  }

  function readSessionId() {
    var subtitle = document.querySelector(".page-subtitle");
    var text = subtitle ? subtitle.textContent || "" : "";
    var match = text.match(/会话\s+([0-9a-fA-F]+)/);
    return match ? match[1] : "";
  }

  function nativePrompt(title, body, hidden) {
    if (hidden) {
      return window.prompt(title + (body ? "\n\n" + body : ""), "") || "";
    }
    return window.prompt(title + (body ? "\n\n" + body : ""), "") || "";
  }

  function approvalChoice(payload) {
    var body = [
      "检测到需要确认的命令操作。",
      "",
      "原因: " + String(payload.description || "需要用户审批"),
      "",
      "命令:",
      String(payload.command || ""),
      "",
      "输入 o/once 允许本次，s/session 允许本会话，a/always 永久允许，d/deny 拒绝。",
      "留空或取消将拒绝。"
    ].join("\n");
    var raw = nativePrompt("需要工具审批", body, false).trim().toLowerCase();
    if (raw === "o" || raw === "once" || raw === "1") return "once";
    if (raw === "s" || raw === "session" || raw === "2") return "session";
    if (raw === "a" || raw === "always" || raw === "3") return "always";
    return "deny";
  }

  function handleGatewayEvent(type, payload, eventSessionId) {
    payload = payload || {};
    var requestId = payload.request_id || payload.requestId || payload.id || "";
    var sessionId = payload.session_id || eventSessionId || readSessionId();

    if (!sessionId) return;
    if (!requestId && type !== "approval.request") return;
    if (pendingPrompt === requestId) return;

    if (type === "clarify.request") {
      pendingPrompt = requestId;
      var choices = Array.isArray(payload.choices) && payload.choices.length
        ? "\n\n选项:\n" + payload.choices.map(function (item, index) {
            return (index + 1) + ". " + String(item);
          }).join("\n")
        : "";
      var answer = nativePrompt("需要补充信息", String(payload.question || "") + choices, false);
      pendingPrompt = null;
      rpc("clarify.respond", { session_id: sessionId, request_id: requestId, answer: answer });
      return;
    }

    if (type === "sudo.request") {
      pendingPrompt = requestId;
      var password = nativePrompt("需要 sudo 密码", "该任务需要提权继续。留空表示取消。", true);
      pendingPrompt = null;
      rpc("sudo.respond", { session_id: sessionId, request_id: requestId, password: password });
      return;
    }

    if (type === "secret.request") {
      pendingPrompt = requestId;
      var label = payload.prompt || payload.env_var || "请输入密钥或令牌";
      var value = nativePrompt("需要敏感信息", String(label) + "\n留空表示取消。", true);
      pendingPrompt = null;
      rpc("secret.respond", { session_id: sessionId, request_id: requestId, value: value });
      return;
    }

    if (type === "approval.request") {
      pendingPrompt = requestId || "approval:" + sessionId;
      var choice = approvalChoice(payload);
      pendingPrompt = null;
      rpc("approval.respond", { session_id: sessionId, request_id: requestId, choice: choice });
    }
  }

  function installWebSocketBridge() {
    if (!OriginalWebSocket || OriginalWebSocket.__qiqiclawPatched) return;

    function PatchedWebSocket(url, protocols) {
      var ws = protocols === undefined
        ? new OriginalWebSocket(url)
        : new OriginalWebSocket(url, protocols);

      if (isChatSocket(url)) {
        activeChatWs = ws;
        var originalSend = ws.send.bind(ws);
        ws.send = function (data) {
          var req;
          try {
            req = typeof data === "string" ? JSON.parse(data) : null;
          } catch (_) {
            req = null;
          }

          if (
            req &&
            req.method === "prompt.submit" &&
            req.params &&
            typeof req.params.text === "string" &&
            req.params.text.trim() &&
            !req.params.__qiqiclawDropNormalized &&
            !/^\[User attached (file|image):/.test(req.params.text.trim())
          ) {
            var detectId = nextRpcId();
            pendingDropSubmits[detectId] = req;
            originalSend(JSON.stringify({
              jsonrpc: "2.0",
              id: detectId,
              method: "input.detect_drop",
              params: {
                session_id: req.params.session_id,
                text: req.params.text
              }
            }));
            return;
          }

          return originalSend(data);
        };
        ws.addEventListener("message", function (event) {
          var msg;
          try {
            msg = JSON.parse(event.data);
          } catch (_) {
            return;
          }
          if (msg && Object.prototype.hasOwnProperty.call(pendingDropSubmits, msg.id)) {
            var original = pendingDropSubmits[msg.id];
            delete pendingDropSubmits[msg.id];
            if (original && ws.readyState === 1) {
              var normalized = JSON.parse(JSON.stringify(original));
              normalized.params.__qiqiclawDropNormalized = true;
              if (msg.result && msg.result.matched && msg.result.text) {
                normalized.params.text = msg.result.text;
              }
              sendRaw(ws, JSON.stringify(normalized));
            }
            return;
          }
          if (msg && msg.method === "event" && msg.params) {
            handleGatewayEvent(msg.params.type, msg.params.payload, msg.params.session_id);
          }
        });
        ws.addEventListener("close", function () {
          if (activeChatWs === ws) activeChatWs = null;
        });
      }

      return ws;
    }

    PatchedWebSocket.prototype = OriginalWebSocket.prototype;
    Object.setPrototypeOf(PatchedWebSocket, OriginalWebSocket);
    PatchedWebSocket.__qiqiclawPatched = true;
    window.WebSocket = PatchedWebSocket;
  }

  function quotePath(path) {
    path = String(path || "").trim();
    if (path.indexOf("file://") === 0) {
      try {
        path = decodeURIComponent(new URL(path).pathname);
      } catch (_) {
        path = path.replace(/^file:\/\//, "");
      }
    }
    if (!path) return "";
    return /\s/.test(path) ? JSON.stringify(path) : path;
  }

  function filePathsFromDrop(event) {
    var files = Array.prototype.slice.call(event.dataTransfer && event.dataTransfer.files || []);
    var paths = files.map(function (file) {
      return quotePath(file.path || file.webkitRelativePath || file.name || "");
    }).filter(Boolean);
    var dt = event.dataTransfer;
    if (dt) {
      ["text/uri-list", "text/plain"].forEach(function (type) {
        try {
          var text = dt.getData(type);
          if (!text) return;
          text.split(/\r?\n/).forEach(function (line) {
            line = line.trim();
            if (!line || line.charAt(0) === "#") return;
            if (line.indexOf("file://") === 0 || line.charAt(0) === "/") {
              var p = quotePath(line);
              if (p) paths.push(p);
            }
          });
        } catch (_) {}
      });
    }
    return paths.filter(function (path, index, arr) {
      return arr.indexOf(path) === index;
    });
  }

  function findChatTextarea(target) {
    if (target && target.closest) {
      var direct = target.closest("textarea.chat-input");
      if (direct) return direct;
      var bar = target.closest(".chat-input-bar");
      if (bar) {
        var scoped = bar.querySelector("textarea.chat-input");
        if (scoped) return scoped;
      }
    }
    return document.querySelector("textarea.chat-input");
  }

  function setTextareaValue(textarea, value) {
    var proto = Object.getPrototypeOf(textarea);
    var desc = Object.getOwnPropertyDescriptor(proto, "value");
    if (desc && desc.set) desc.set.call(textarea, value);
    else textarea.value = value;
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function insertPaths(textarea, paths) {
    if (!textarea || !paths.length) return;
    var current = textarea.value || "";
    var start = textarea.selectionStart == null ? current.length : textarea.selectionStart;
    var end = textarea.selectionEnd == null ? current.length : textarea.selectionEnd;
    var insert = paths.join(" ");
    var prefix = start > 0 && !/\s$/.test(current.slice(0, start)) ? " " : "";
    var suffix = end < current.length && !/^\s/.test(current.slice(end)) ? " " : "";
    var next = current.slice(0, start) + prefix + insert + suffix + current.slice(end);
    setTextareaValue(textarea, next);
    var pos = start + prefix.length + insert.length + suffix.length;
    textarea.focus();
    textarea.setSelectionRange(pos, pos);
  }

  function installDropPathBridge() {
    document.addEventListener("dragover", function (event) {
      if (findChatTextarea(event.target)) {
        event.preventDefault();
      }
    }, true);

    document.addEventListener("drop", function (event) {
      var textarea = findChatTextarea(event.target);
      if (!textarea) return;
      var paths = filePathsFromDrop(event);
      if (!paths.length) return;
      event.preventDefault();
      event.stopPropagation();
      insertPaths(textarea, paths);
    }, true);
  }

  installWebSocketBridge();
  installDropPathBridge();
})();
