import threading
import json
import time
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText
import urllib.request
import urllib.error

try:
    import requests
except ImportError:
    requests = None


class ChatApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Free AI Chat")
        self.root.geometry("720x560")

        self.history = []
        self.recent_models = []
        self._last_tags_fetch = 0.0
        self._tags_cache = []
        self._first_request = True

        self._build_ui()

    def _build_ui(self) -> None:
        settings = ttk.LabelFrame(self.root, text="API Settings")
        settings.pack(fill="x", padx=10, pady=8)

        ttk.Label(settings, text="Ollama URL").grid(row=0, column=0, padx=6, pady=6, sticky="w")
        self.base_url_var = tk.StringVar(value="http://localhost:11434")
        self.base_url_entry = ttk.Entry(settings, textvariable=self.base_url_var, width=45)
        self.base_url_entry.grid(row=0, column=1, padx=6, pady=6, sticky="we")

        ttk.Label(settings, text="Model").grid(row=0, column=2, padx=6, pady=6, sticky="w")
        self.model_var = tk.StringVar(value="llama3.1:8b")
        self.model_combo = ttk.Combobox(
            settings,
            textvariable=self.model_var,
            width=18,
            values=[],
        )
        self.model_combo.grid(row=0, column=3, padx=6, pady=6, sticky="we")
        self.model_combo.bind("<Button-1>", self._refresh_models)
        self.model_combo.bind("<FocusIn>", self._refresh_models)

        self.refresh_button = ttk.Button(
            settings, text="Refresh Models", command=self._refresh_models
        )
        self.refresh_button.grid(row=0, column=4, padx=6, pady=6, sticky="we")

        self.system_var = tk.StringVar(value="You are a helpful assistant.")
        ttk.Label(settings, text="System Prompt").grid(
            row=1, column=0, padx=6, pady=6, sticky="w"
        )
        self.system_entry = ttk.Entry(settings, textvariable=self.system_var, width=45)
        self.system_entry.grid(row=1, column=1, padx=6, pady=6, sticky="we", columnspan=3)

        settings.columnconfigure(1, weight=1)
        settings.columnconfigure(3, weight=1)
        settings.columnconfigure(4, weight=0)

        chat_frame = ttk.Frame(self.root)
        chat_frame.pack(fill="both", expand=True, padx=10, pady=6)

        self.chat_display = ScrolledText(chat_frame, wrap="word", state="disabled")
        self.chat_display.pack(fill="both", expand=True)

        input_frame = ttk.Frame(self.root)
        input_frame.pack(fill="x", padx=10, pady=8)

        self.user_input = ttk.Entry(input_frame)
        self.user_input.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.user_input.bind("<Return>", self._on_send)

        self.refresh_context_button = ttk.Button(
            input_frame, text="Refresh Context", command=self._refresh_context
        )
        self.refresh_context_button.pack(side="right", padx=(0, 8))

        self.send_button = ttk.Button(input_frame, text="Send", command=self._on_send)
        self.send_button.pack(side="right")

        self._append_chat(
            "System",
            "Ensure Ollama is running locally (default http://localhost:11434).",
        )
        self._refresh_models()

    def _append_chat(self, speaker: str, text: str) -> None:
        self.chat_display.configure(state="normal")
        self.chat_display.insert("end", f"{speaker}: {text}\n\n")
        self.chat_display.configure(state="disabled")
        self.chat_display.see("end")

    def _on_send(self, _event=None) -> None:
        message = self.user_input.get().strip()
        if not message:
            return
        self.user_input.delete(0, "end")

        self._append_chat("You", message)
        self.send_button.configure(state="disabled")

        if not self.history:
            system_prompt = self.system_var.get().strip()
            if system_prompt:
                self.history.append({"role": "system", "content": system_prompt})

        self.history.append({"role": "user", "content": message})

        thread = threading.Thread(target=self._call_api, daemon=True)
        thread.start()

    def _call_api(self) -> None:
        base_url = self.base_url_var.get().strip().rstrip("/")
        model = self.model_var.get().strip()

        if not base_url or not model:
            self._post_error(
                "Missing Ollama URL or model. Fill those in and try again."
            )
            return

        url = f"{base_url}/api/chat"
        headers = {"Content-Type": "application/json"}
        payload = {
            "model": model,
            "messages": self.history,
            "stream": False,
            "options": {"temperature": 0.7},
        }

        timeout_seconds = self._compute_timeout(model)
        try:
            if requests is not None:
                response = requests.post(
                    url, headers=headers, json=payload, timeout=timeout_seconds
                )
                status = response.status_code
                response_text = response.text
            else:
                request_data = json.dumps(payload).encode("utf-8")
                request = urllib.request.Request(
                    url,
                    data=request_data,
                    headers=headers,
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                    status = response.status
                    response_text = response.read().decode("utf-8")

            if status != 200:
                self._post_error(
                    f"API error {status}: {response_text.strip()}"
                )
                return

            data = json.loads(response_text)
            content = data.get("message", {}).get("content", "").strip()
            if not content:
                self._post_error("No response content returned.")
                return

            self.history.append({"role": "assistant", "content": content})
            self._track_recent_model(model)
            self.root.after(0, lambda: self._append_chat("AI", content))
            self._first_request = False
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            self._post_error(f"API error {exc.code}: {body}")
        except Exception as exc:  # noqa: BLE001
            self._post_error(f"Request failed: {exc}")
        finally:
            self.root.after(0, lambda: self.send_button.configure(state="normal"))

    def _post_error(self, message: str) -> None:
        self.root.after(0, lambda: self._append_chat("Error", message))
        self.root.after(0, lambda: self.send_button.configure(state="normal"))

    def _compute_timeout(self, model: str) -> int:
        normalized = model.strip().lower()
        if normalized.startswith("qwen3"):
            return 300 if self._first_request else 120
        return 120 if self._first_request else 30

    def _refresh_context(self) -> None:
        self.history = []
        self.chat_display.configure(state="normal")
        self.chat_display.delete("1.0", "end")
        self.chat_display.configure(state="disabled")
        self._append_chat(
            "System",
            "Context refreshed. New messages will start a new conversation.",
        )

    def _track_recent_model(self, model: str) -> None:
        if not model:
            return
        if model in self.recent_models:
            self.recent_models.remove(model)
        self.recent_models.insert(0, model)
        self.recent_models = self.recent_models[:5]
        self.root.after(0, self._update_model_list)

    def _refresh_models(self, _event=None) -> None:
        now = time.time()
        if now - self._last_tags_fetch < 5:
            return
        self._last_tags_fetch = now
        print("Fetching models...")
        thread = threading.Thread(target=self._fetch_models, daemon=True)
        print("Starting thread to fetch models...")
        thread.start()
        print("models fetched")

    def _fetch_models(self) -> None:
        base_url = self.base_url_var.get().strip().rstrip("/")
        if not base_url:
            return
        url = f"{base_url}/api/tags"
        try:
            request = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(request, timeout=10) as response:
                response_text = response.read().decode("utf-8")
            data = json.loads(response_text)
            models = [m.get("name", "") for m in data.get("models", []) if m.get("name")]
            print("z", models)
            self._tags_cache = models
            self.root.after(0, self._update_model_list)
        except Exception:
            return

    def _update_model_list(self) -> None:
        combined = []
        for name in self.recent_models:
            if name not in combined:
                combined.append(name)
        for name in self._tags_cache:
            if name not in combined:
                combined.append(name)
        if combined:
            self.model_combo["values"] = combined


def main() -> None:
    root = tk.Tk()
    style = ttk.Style(root)
    if "vista" in style.theme_names():
        style.theme_use("vista")
    app = ChatApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
