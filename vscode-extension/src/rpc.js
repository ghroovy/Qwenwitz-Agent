// SPDX-License-Identifier: MIT
"use strict";

const { spawn } = require("child_process");
const path = require("path");

class Hoi4Rpc {
  constructor(execPath, serverPath, extraEnv = {}) {
    this.execPath = execPath;
    this.serverPath = serverPath;
    this.extraEnv = extraEnv;
    this.proc = null;
    this.nextId = 1;
    this.pending = new Map();
    this.buffer = "";
  }

  start() {
    if (this.proc) return;
    this.proc = spawn(this.execPath, [this.serverPath], {
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
      cwd: path.dirname(path.dirname(this.serverPath)),  // repo root
      env: { ...process.env, ...this.extraEnv },
    });
    this.proc.stdout.on("data", (chunk) => {
      this.buffer += chunk.toString("utf8");
      let idx;
      while ((idx = this.buffer.indexOf("\n")) >= 0) {
        const line = this.buffer.slice(0, idx).trim();
        this.buffer = this.buffer.slice(idx + 1);
        if (!line) continue;
        try {
          const msg = JSON.parse(line);
          const entry = this.pending.get(msg.id);
          if (entry) {
            this.pending.delete(msg.id);
            if (msg.error) entry.reject(new Error(msg.error.message));
            else entry.resolve(msg.result);
          }
        } catch (err) {
          console.error("[hoi4] bad rpc line", err.message);
        }
      }
    });
    this.proc.stderr.on("data", (d) => console.error("[hoi4-server]", d.toString().trim()));
    this.proc.on("exit", () => {
      this.proc = null;
      for (const entry of this.pending.values()) entry.reject(new Error("server exited"));
      this.pending.clear();
    });
  }

  call(method, params = {}, timeoutMs = 300000) {
    this.start();
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`timeout: ${method}`));
      }, timeoutMs);
      this.pending.set(id, {
        resolve: (v) => { clearTimeout(timer); resolve(v); },
        reject: (e) => { clearTimeout(timer); reject(e); },
      });
      this.proc.stdin.write(JSON.stringify({ id, method, params }) + "\n");
    });
  }

  stop() {
    if (this.proc) {
      this.proc.kill();
      this.proc = null;
    }
  }
}

function defaultPythonPath(repoRoot) {
  const candidates = [
    process.env.HOI4_AGENT_PYTHON,
    path.join(repoRoot, ".venv", "Scripts", "python.exe"),
    path.join(repoRoot, ".venv", "bin", "python"),
    "python",
  ].filter(Boolean);
  return candidates[0];
}

module.exports = { Hoi4Rpc, defaultPythonPath };
