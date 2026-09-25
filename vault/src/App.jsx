import { useState, useEffect, useRef } from "react";
import {
  Cloud, UploadCloud, ShieldCheck, ArrowRight, Folder, HardDrive, Coffee,
  LayoutDashboard, Settings, Search, Menu, X, FileText,
  Image as ImageIcon, FileCode, File, Download, RefreshCw, Plus,
  CheckCircle2, LockKeyhole, Zap, ChevronRight, LogOut, Eye
} from "lucide-react";
import "./index.css";

const API_URL = "http://127.0.0.1:8000";

function formatSize(bytes = 0) {
  bytes = Number(bytes) || 0;
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function getFileIcon(filename = "") {
  const ext = filename.split(".").pop().toLowerCase();
  if (["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(ext)) return ImageIcon;
  if (["js", "jsx", "py", "html", "css", "json", "java", "cpp", "c"].includes(ext)) return FileCode;
  if (["pdf", "doc", "docx", "txt", "ppt", "pptx"].includes(ext)) return FileText;
  return File;
}

export default function App() {
  const [page, setPage] = useState("home");
  const [active, setActive] = useState("Overview");
  const [files, setFiles] = useState([]);
  const [token, setToken] = useState(localStorage.getItem("khazana_token") || "");
  const [user, setUser] = useState(null);
  const [authMode, setAuthMode] = useState("login");
  const [authForm, setAuthForm] = useState({ name: "", email: "", password: "" });
  const [authLoading, setAuthLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadMessage, setUploadMessage] = useState("");
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const fileInput = useRef(null);

  const authHeaders = () => ({ Authorization: `Bearer ${token}` });

  function logout() {
    localStorage.removeItem("khazana_token");
    setToken("");
    setUser(null);
    setFiles([]);
    setError("");
    setUploadMessage("");
    setPage("home");
    setAuthMode("login");
    setAuthForm({ name: "", email: "", password: "" });
  }

  async function fetchFiles() {
    if (!token) return;
    try {
      setError("");
      const response = await fetch(`${API_URL}/api/files`, { headers: authHeaders() });
      if (response.status === 401 || response.status === 403) {
        logout();
        setError("Your session expired. Please log in again.");
        return;
      }
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Could not load files.");
      setFiles(Array.isArray(data) ? data : data.files || []);
    } catch (err) {
      setError(err.message || "Can't connect to the server. Please start the backend.");
    }
  }

  async function loadUser(accessToken = token) {
    if (!accessToken) return;
    try {
      const response = await fetch(`${API_URL}/api/me`, {
        headers: { Authorization: `Bearer ${accessToken}` },
      });
      if (response.status === 401 || response.status === 403) {
        localStorage.removeItem("khazana_token");
        setToken("");
        setPage("home");
        return;
      }
      if (response.ok) {
        const data = await response.json();
        setUser(data.user || data);
      }
    } catch (err) {
      console.error("Could not load profile", err);
    }
  }

  useEffect(() => {
    if (token) {
      setPage("dashboard");
      loadUser(token);
      fetchFiles();
    }
    // Validate the saved session once when the app loads.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (page === "dashboard" && token) fetchFiles();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, token]);

  async function handleAuth(e) {
    e.preventDefault();
    setAuthLoading(true);
    setError("");
    try {
      const signup = authMode === "signup";
      const endpoint = signup ? "/api/register" : "/api/login";
      const payload = signup
        ? { name: authForm.name.trim(), email: authForm.email.trim(), password: authForm.password }
        : { email: authForm.email.trim(), password: authForm.password };
      const response = await fetch(`${API_URL}${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || data.message || "Authentication failed.");
      const accessToken = data.access_token || data.token || data.accessToken;
      if (!accessToken) {
        if (signup) {
          setAuthMode("login");
          setError("Account created. Please log in with your email and password.");
          return;
        }
        throw new Error("Backend response did not contain an access token. Check the login response format.");
      }
      localStorage.setItem("khazana_token", accessToken);
      setToken(accessToken);
      setUser(data.user || null);
      setAuthForm({ name: "", email: authForm.email, password: "" });
      setPage("dashboard");
      await loadUser(accessToken);
      await fetchFilesWithToken(accessToken);
    } catch (err) {
      setError(err.message || "Could not connect to the backend.");
    } finally {
      setAuthLoading(false);
    }
  }

  async function fetchFilesWithToken(accessToken) {
    try {
      const response = await fetch(`${API_URL}/api/files`, {
        headers: { Authorization: `Bearer ${accessToken}` },
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Could not load your files.");
      setFiles(Array.isArray(data) ? data : data.files || []);
    } catch (err) {
      setError(err.message || "Could not load files.");
    }
  }

  async function handleUpload(event) {
    const selectedFiles = Array.from(event.target.files || []);
    if (!selectedFiles.length || !token) return;
    setUploading(true);
    setUploadMessage("");
    setError("");
    let successCount = 0;
    try {
      for (const file of selectedFiles) {
        const formData = new FormData();
        formData.append("file", file);
        const response = await fetch(`${API_URL}/api/upload`, {
          method: "POST",
          headers: authHeaders(),
          body: formData,
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(result.detail || `Upload failed for ${file.name}.`);
        successCount++;
      }
      setUploadMessage(`${successCount} file(s) uploaded successfully!`);
      await fetchFiles();
    } catch (err) {
      setError(err.message || "Upload failed. Please try again.");
    } finally {
      setUploading(false);
      event.target.value = "";
    }
  }

  async function openFile(file) {
    const id = file.id;
    if (id == null) {
      setError("File ID is missing from the backend response.");
      return;
    }
    try {
      setError("");
      const response = await fetch(`${API_URL}/api/files/${encodeURIComponent(id)}/open`, {
        headers: authHeaders(),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail || "Could not open this file.");
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const filename = file.filename || file.name || "download";
      const ext = filename.split(".").pop().toLowerCase();
      const previewable = ["pdf", "png", "jpg", "jpeg", "gif", "webp", "svg", "txt", "html"].includes(ext);
      if (previewable) {
        const tab = window.open("about:blank", "_blank");
        if (tab) {
          tab.document.title = filename;
          const frame = tab.document.createElement("iframe");
          frame.src = url;
          frame.style.cssText = "border:0;width:100%;height:100vh";
          tab.document.body.style.cssText = "margin:0";
          tab.document.body.appendChild(frame);
          tab.addEventListener("beforeunload", () => URL.revokeObjectURL(url), { once: true });
        } else {
          const link = document.createElement("a");
          link.href = url; link.download = filename; link.click();
          setTimeout(() => URL.revokeObjectURL(url), 60000);
        }
      } else {
        const link = document.createElement("a");
        link.href = url; link.download = filename;
        document.body.appendChild(link); link.click(); link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 60000);
      }
    } catch (err) {
      setError(err.message || "Unable to open file.");
    }
  }

  const filteredFiles = files.filter((file) =>
    (file.filename || file.name || "").toLowerCase().includes(search.toLowerCase())
  );
  const totalBytes = files.reduce((total, file) => total + Number(file.size || 0), 0);
  const navItems = [
    { label: "Overview", icon: LayoutDashboard },
    { label: "My Files", icon: Folder },
    { label: "Storage", icon: HardDrive },
  ];
  const displayName = user?.name || user?.full_name || user?.email?.split("@")[0] || "My Khazana";

  if (page === "auth") {
    return (
      <div className="khazana-home">
        <nav className="landing-nav">
          <button className="brand" onClick={() => { setPage("home"); setError(""); }}>
            <span className="brand-icon"><Cloud size={23} /></span>
            <span>khazana<span className="brand-dot">.</span></span>
          </button>
          <button className="nav-cta" onClick={() => setPage("home")}>Back home <ArrowRight size={16} /></button>
        </nav>
        <main className="auth-page" style={{ maxWidth: 520, margin: "55px auto", padding: 24 }}>
          <div className="panel auth-panel" style={{ padding: 32, borderRadius: 20 }}>
            <div className="feature-icon" style={{ marginBottom: 18 }}><LockKeyhole size={24} /></div>
            <span className="hero-badge">YOUR PERSONAL DIGITAL SPACE</span>
            <h1 style={{ marginTop: 18 }}>{authMode === "signup" ? "Create your Khazana." : "Welcome back."}</h1>
            <p className="subtitle">{authMode === "signup" ? "Make a personal space for your files." : "Log in to access your personal files."}</p>
            <form onSubmit={handleAuth} style={{ display: "grid", gap: 16, marginTop: 24 }}>
              {authMode === "signup" && (
                <label className="auth-field" style={{ display: "grid", gap: 7 }}>
                  <span>Full name</span>
                  <input required autoComplete="name" value={authForm.name} onChange={(e) => setAuthForm({ ...authForm, name: e.target.value })} placeholder="Your name" />
                </label>
              )}
              <label className="auth-field" style={{ display: "grid", gap: 7 }}>
                <span>Email address</span>
                <input required type="email" autoComplete="email" value={authForm.email} onChange={(e) => setAuthForm({ ...authForm, email: e.target.value })} placeholder="you@example.com" />
              </label>
              <label className="auth-field" style={{ display: "grid", gap: 7 }}>
                <span>Password</span>
                <input required type="password" minLength={6} autoComplete={authMode === "signup" ? "new-password" : "current-password"} value={authForm.password} onChange={(e) => setAuthForm({ ...authForm, password: e.target.value })} placeholder="At least 6 characters" />
              </label>
              {error && <div className="notice error-notice" role="alert">{error}</div>}
              <button className="primary-button" type="submit" disabled={authLoading} style={{ justifyContent: "center" }}>
                {authLoading ? "Please wait..." : authMode === "signup" ? "Create account" : "Log in"} <ArrowRight size={18} />
              </button>
            </form>
            <p style={{ textAlign: "center", marginTop: 22 }}>
              {authMode === "signup" ? "Already have an account? " : "New to Khazana? "}
              <button className="text-button" type="button" onClick={() => { setAuthMode(authMode === "signup" ? "login" : "signup"); setError(""); }}>
                {authMode === "signup" ? "Log in" : "Create account"}
              </button>
            </p>
          </div>
        </main>
        <footer className="landing-footer"><span className="footer-brand"><Cloud size={18} /> khazana.</span><span>Made with care, for your digital world.</span></footer>
      </div>
    );
  }

  if (page === "home") {
    return (
      <div className="khazana-home">
        <nav className="landing-nav">
          <button className="brand" onClick={() => setPage("home")}><span className="brand-icon"><Cloud size={23} /></span><span>khazana<span className="brand-dot">.</span></span></button>
          <div className="landing-links"><a href="#features">Features</a><a href="#about">About</a></div>
          <button className="nav-cta" onClick={() => { setAuthMode(token ? "login" : "login"); setPage(token ? "dashboard" : "auth"); }}>Open my vault <ArrowRight size={16} /></button>
        </nav>
        <main>
          <section className="hero-section">
            <div className="hero-copy">
              <div className="hero-badge"><span className="hero-badge-dot" /> YOUR PERSONAL DIGITAL SPACE</div>
              <h1>Your files.<br />Your world.<br /><span>One Khazana.</span></h1>
              <p className="hero-description">A little home for everything that matters. Store your files, keep them organized, and access your digital world in one place.</p>
              <div className="hero-buttons"><button className="primary-button" onClick={() => { setAuthMode("signup"); setError(""); setPage("auth"); }}>Explore my Khazana <ArrowRight size={18} /></button><a href="#features" className="secondary-button">Discover more</a></div>
              <div className="hero-trust"><span><ShieldCheck size={17} /> Personal storage</span><span><LockKeyhole size={16} /> Your files, your space</span></div>
            </div>
            <div className="hero-visual"><div className="visual-glow" /><div className="storage-window"><div className="window-top"><div className="window-dots"><i /><i /><i /></div><span>My digital space</span><Cloud size={18} /></div><div className="window-content"><div className="window-greeting"><div><span className="window-label">YOUR SPACE</span><h3>A place for everything.</h3></div><div className="window-cloud-icon"><Coffee size={23} /></div></div><div className="storage-illustration"><div className="storage-ring"><div className="storage-ring-inner"><Coffee size={38} /></div></div><div className="ring-label"><strong>Khazana</strong><span>Your files, together</span></div></div><div className="preview-files"><div className="preview-file"><div className="preview-file-icon pdf-icon"><FileText size={20} /></div><div><strong>My documents</strong><span>Keep things organized</span></div><ChevronRight size={17} /></div><div className="preview-file"><div className="preview-file-icon image-icon"><ImageIcon size={20} /></div><div><strong>Memories & photos</strong><span>Your favorite moments</span></div><ChevronRight size={17} /></div></div></div></div><div className="floating-card floating-card-top"><div className="floating-icon"><ShieldCheck size={20} /></div><div><strong>Your space</strong><span>Ready for your files</span></div><CheckCircle2 size={19} className="floating-check" /></div><div className="floating-card floating-card-bottom"><div className="floating-icon upload-float-icon"><UploadCloud size={21} /></div><div><strong>One simple upload</strong><span>Bring it all together</span></div></div></div>
          </section>
          <section className="feature-strip" id="features"><div className="feature-item"><div className="feature-icon"><Cloud size={22} /></div><div><h3>Your own space</h3><p>A personal home for your digital files.</p></div></div><div className="feature-item"><div className="feature-icon"><Folder size={22} /></div><div><h3>Everything organized</h3><p>Find your documents in one place.</p></div></div><div className="feature-item"><div className="feature-icon"><LockKeyhole size={22} /></div><div><h3>Built around you</h3><p>Your files, managed in your workspace.</p></div></div></section>
          <section className="landing-bottom" id="about"><div><span className="hero-badge">A LITTLE SPACE. A LOT OF POSSIBILITIES.</span><h2>Make room for what matters.</h2><p>From everyday documents to your favorite memories, give your digital world a home.</p></div><button className="primary-button" onClick={() => { setAuthMode("signup"); setPage("auth"); }}>Get started <ArrowRight size={18} /></button></section>
        </main>
        <footer className="landing-footer"><span className="footer-brand"><Cloud size={18} /> khazana.</span><span>Made with care, for your digital world.</span></footer>
      </div>
    );
  }

  return (
    <div className="app-shell">
      <main className="main-content">
        <header className="topbar"><div className="topbar-brand"><Coffee size={25} strokeWidth={2.5} /><span>KHAZANA</span></div><div className="topbar-actions"><label className="search-box"><Search size={17} /><input placeholder="Search your files..." value={search} onChange={(e) => setSearch(e.target.value)} /></label><button className="icon-button" onClick={fetchFiles} title="Refresh files"><RefreshCw size={19} /></button><button className="top-avatar" onClick={logout} title="Log out"><LogOut size={17} /></button></div></header>
        <div className="dashboard"><section className="welcome-row"><div><div className="eyebrow"><span className="live-dot" /> YOUR PERSONAL CLOUD</div><h1>{active === "Overview" ? `Welcome, ${displayName}.` : active}</h1><p className="subtitle">Everything you upload, all in one little place.</p></div><button className="upload-button" disabled={uploading} onClick={() => fileInput.current?.click()}><Plus size={18} />{uploading ? "Uploading..." : "Upload files"}</button><input ref={fileInput} type="file" multiple hidden onChange={handleUpload} /></section>
          {uploadMessage && <div className="notice success-notice" role="status"><CheckCircle2 size={18} /> {uploadMessage}</div>}{error && <div className="notice error-notice" role="alert">{error} <button onClick={fetchFiles}>Retry</button></div>}
          <section className="stats-grid"><div className="stat-card"><div className="stat-top"><div className="stat-icon sage-icon"><Coffee size={20} /></div><span className="stat-tag">Your workspace</span></div><p className="stat-label">Total storage used</p><div className="stat-value">{formatSize(totalBytes)}</div><div className="stat-foot">Calculated from uploaded files</div></div><div className="stat-card"><div className="stat-top"><div className="stat-icon beige-icon"><Folder size={20} /></div><span className="stat-tag">Your uploads</span></div><p className="stat-label">Total files</p><div className="stat-value">{files.length}</div><div className="stat-foot">Files stored in your workspace</div></div><div className="stat-card"><div className="stat-top"><div className="stat-icon rose-icon"><ShieldCheck size={20} /></div><span className="stat-tag">Protected</span></div><p className="stat-label">Storage status</p><div className="stat-value stat-status">{error ? "Check connection" : "Connected"}</div><div className="stat-foot">Based on backend response</div></div></section>
          <section className="panel files-panel"><div className="panel-heading"><div><h2>{active === "Storage" ? "Storage overview" : "Your files"}</h2><p>Your uploaded files, in one place.</p></div><button className="text-button" onClick={fetchFiles}><RefreshCw size={15} /> Refresh</button></div>
            {filteredFiles.length === 0 ? <div className="empty-state"><div className="empty-icon"><UploadCloud size={30} /></div><h3>{search ? "No matching files" : "Your Khazana is waiting"}</h3><p>{search ? "Try a different search term." : "Upload your first file and it will appear here."}</p>{!search && <button className="upload-button" onClick={() => fileInput.current?.click()} disabled={uploading}><Plus size={17} /> Upload your first file</button>}</div> : <div className="file-table"><div className="file-table-head"><span>Name</span><span>Size</span><span>File type</span></div>{filteredFiles.map((file, index) => { const filename = file.filename || file.name || "Untitled"; const Icon = getFileIcon(filename); return <div className="file-row" key={file.id || `${filename}-${index}`}><div className="file-name"><div className="file-icon sage"><Icon size={19} /></div><div><strong>{filename}</strong><span>Stored in Khazana</span></div></div><span className="file-size">{formatSize(file.size)}</span><span className="file-type">{filename.includes(".") ? filename.split(".").pop().toUpperCase() : "FILE"}</span><button className="icon-button" title="Open or download" onClick={() => openFile(file)}><Eye size={17} /></button></div>; })}</div>}
          </section><footer className="dashboard-footer"><button className="back-home" onClick={() => setPage("home")}><Cloud size={16} /> Back to Khazana home</button><span>Made with care, kept in your space.</span></footer>
        </div>
      </main>
    </div>
  );
}
