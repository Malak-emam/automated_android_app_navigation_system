//server.js
require('dotenv').config();
const express = require('express');
const session = require('express-session');
const http = require('http');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const multer = require('multer');
const WebSocket = require('ws');
const sqlite3 = require('sqlite3').verbose();
const bcrypt = require('bcrypt');
const SALT_ROUNDS = 10;
const passport = require('passport');
const GoogleStrategy = require('passport-google-oauth20').Strategy;


const dbPath = path.join(__dirname, '../users.db');
const db = new sqlite3.Database(dbPath);

// Create users table if it doesn't exist
db.run(`
  CREATE TABLE IF NOT EXISTS user (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password TEXT NOT NULL
  )
`);

const app = express();
const server = http.createServer(app);
const wss = new WebSocket.Server({ server });

app.use(express.static(path.join(__dirname, '../public')));
app.use(express.json()); // ✅ Add this
app.use(express.urlencoded({ extended: true })); // ✅ Add this

app.use(session({
  resave: false,
  saveUninitialized: true,
  secret: process.env.SESSION_SECRET || 'my-default-secret'
}));

app.use(passport.initialize());
app.use(passport.session());

passport.use(new GoogleStrategy({
  clientID: process.env.GOOGLE_OAUTH_CLIENT_ID,
  clientSecret: process.env.GOOGLE_OAUTH_CLIENT_SECRET,
  callbackURL: "/auth/google/callback"
}, function(accessToken, refreshToken, profile, done) {
  // Store user info in session (or DB if needed)
  const user = {
    email: profile.emails[0].value,
    name: profile.displayName
  };
  return done(null, user);
}));

passport.serializeUser((user, done) => {
  done(null, user);
});
passport.deserializeUser((obj, done) => {
  done(null, obj);
});


// APK upload
const UPLOADS_DIR = path.join(__dirname, '../uploaded_apks');
if (!fs.existsSync(UPLOADS_DIR)) fs.mkdirSync(UPLOADS_DIR);

const upload = multer({ dest: UPLOADS_DIR });
app.post('/api/upload-apk', upload.single('apk'), (req, res) => {
  if (!req.file) return res.status(400).json({ success: false, error: 'No file uploaded' });
  const destPath = path.join(UPLOADS_DIR, req.file.originalname);
  fs.renameSync(req.file.path, destPath);
  console.log(`✅ Uploaded APK saved at ${destPath}`);
  res.json({ success: true, apkName: req.file.originalname });
});

// Start testing
app.post('/api/start-testing', (req, res) => {
  console.log("🚀 Starting emulator via PowerShell...");
  const scriptPath = path.join(__dirname, '../start_emu.ps1');

  const ps = spawn("powershell.exe", [
    "-ExecutionPolicy", "Bypass",
    "-File", scriptPath
  ]);

  ps.stdout.on("data", data => console.log(`PS stdout: ${data}`));
  ps.stderr.on("data", data => console.error(`PS stderr: ${data}`));

  ps.on("close", (code) => {
    console.log(`✅ Emulator script exited with code ${code}`);
    // ✅ Respond to frontend AFTER emulator script finishes:
    res.json({ success: true, message: "Emulator ready. Redirecting..." });
  });
});


app.post('/api/start-backend', (req, res) => {
  console.log("🚀 Starting Python backend script...");

  const uploadedApks = fs.readdirSync(UPLOADS_DIR).filter(f => f.endsWith('.apk'));
  if (uploadedApks.length === 0) {
    console.error("❌ No APK found. Make sure you upload one before starting testing.");
    return res.status(500).json({ success: false, message: "No APK uploaded." });
  }

  const { taskFile } = req.body;
  if (!taskFile) {
    console.error("❌ taskFile not specified in request.");
    return res.status(400).json({ success: false, message: "taskFile is missing." });
  }

  const absoluteApkPath = path.join(UPLOADS_DIR, uploadedApks[0]);
  const absoluteTaskFile = path.join(__dirname, '../backend', taskFile);

  if (!fs.existsSync(absoluteTaskFile)) {
    console.error(`❌ Specified task file ${absoluteTaskFile} does not exist.`);
    return res.status(400).json({ success: false, message: "Specified task file does not exist." });
  }

  const backendPath = path.join(__dirname, '../backend/backend.py');
  const python = spawn("python", ["-u", backendPath, absoluteApkPath, absoluteTaskFile]);

  python.stdout.on("data", data => {
    const lines = data.toString().split("\n").map(l => l.trim()).filter(Boolean);
    lines.forEach(line => {
      console.log(`Python stdout: ${line}`);
      wss.clients.forEach(client => {
        if (client.readyState === WebSocket.OPEN) client.send(line);
      });
    });
  });

  python.stderr.on("data", data => console.error(`Python stderr: ${data}`));
  python.on("close", pyCode => console.log(`Python script exited with code ${pyCode}`));

  res.json({ success: true, message: "Python backend started." });
});

// Save manually inputted tasks from user input
app.post('/api/upload-task', (req, res) => {
  console.log("📝 Saving manual task...");
  const tasks = req.body?.tasks;
  if (!tasks || !Array.isArray(tasks)) {
    console.error("❌ Invalid tasks data received.");
    return res.status(400).json({ success: false, error: "Invalid tasks payload." });
  }

  const filePath = path.join(__dirname, '../backend/manual_tasks.json');
  try {
    fs.writeFileSync(filePath, JSON.stringify({ tasks }, null, 2), "utf-8");
    console.log(`✅ Manual task saved to ${filePath}`);
    res.json({ success: true, message: "Manual task saved successfully." });
  } catch (err) {
    console.error("❌ Failed to save manual task:", err);
    res.status(500).json({ success: false, error: "Could not save manual task." });
  }
});


// Serve screenshots
app.use('/screenshots', express.static(path.join(__dirname, '../screenshots')));
const screenshotUpload = multer({ dest: path.join(__dirname, '../screenshots') });
app.post('/api/upload-screenshot', screenshotUpload.single('screenshot'), (req, res) => {
  if (!req.file) return res.status(400).json({ success: false, error: 'No screenshot uploaded' });
  const destPath = path.join(req.file.destination, req.file.originalname);
  fs.renameSync(req.file.path, destPath);
  console.log(`✅ Screenshot saved at ${destPath}`);
  res.json({ success: true });
});

app.post('/api/run-generation', (req, res) => {
  console.log("🚀 Running metadata2.py to generate tasks...");

  // Find the latest uploaded APK
  const uploadedApks = fs.readdirSync(UPLOADS_DIR)
    .filter(f => f.endsWith('.apk'))
    .sort((a, b) => fs.statSync(path.join(UPLOADS_DIR, b)).mtime - fs.statSync(path.join(UPLOADS_DIR, a)).mtime);

  if (uploadedApks.length === 0) {
    console.error("❌ No APK found. Upload one first.");
    return res.status(400).json({ success: false, message: "No APK uploaded." });
  }

  const apkPath = path.join(UPLOADS_DIR, uploadedApks[0]);
  console.log(`✅ Using APK at ${apkPath}`);

  const scriptPath = path.join(__dirname, '../backend/metadata2.py');
  const python = spawn("python", [scriptPath, apkPath]);

  let stdout = '';
  let stderr = '';

  python.stdout.on("data", data => {
    const line = data.toString().trim();
    stdout += line + "\n";
    console.log(`Python stdout: ${line}`);
  });

  python.stderr.on("data", data => {
    const line = data.toString().trim();
    stderr += line + "\n";
    console.error(`Python stderr: ${line}`);
  });

  python.on("close", code => {
    console.log(`✅ metadata2.py exited with code ${code}`);
    if (code === 0) {
      res.json({ success: true, message: "Test cases generated successfully." });
    } else {
      res.status(500).json({ success: false, message: stderr || "metadata2.py failed." });
    }
  });
});

app.get('/metrics-data', (req, res) => {
  const metricsFile = path.join(__dirname, '../backend/metrics_report.json');
  if (!fs.existsSync(metricsFile)) {
    console.error("❌ metrics_report.json does not exist.");
    return res.status(404).json({ error: "Metrics report not found." });
  }

  fs.readFile(metricsFile, "utf-8", (err, data) => {
    if (err) {
      console.error("❌ Failed to read metrics_report.json:", err);
      return res.status(500).json({ error: "Could not read metrics report." });
    }
    try {
      const jsonData = JSON.parse(data);
      res.json(jsonData);
    } catch (parseErr) {
      console.error("❌ Failed to parse metrics_report.json:", parseErr);
      res.status(500).json({ error: "Invalid metrics report format." });
    }
  });
});

// Serve dashboard after signup
app.get('/dashboard', (req, res) => {
  res.sendFile(path.join(__dirname, '../public/dashboard.html')); // 👈 No session check
});

app.get('/reports/:pdfName', (req, res) => {
  const pdfName = req.params.pdfName;
  const pdfPath = path.join(__dirname, '../reports', pdfName);

  if (!fs.existsSync(pdfPath)) {
    console.error("❌ Report PDF not found:", pdfPath);
    return res.status(404).send("Report not found.");
  }

  res.sendFile(pdfPath);
});



// Start server + WebSocket
const PORT = 3000;
server.listen(PORT, () => {
  console.log(`🌐 Server running at http://localhost:${PORT}`);
  console.log(`📡 WebSocket listening on ws://localhost:${PORT}`);
});

app.post('/api/signup', async (req, res) => {
  const { first_name, last_name, email, password } = req.body;

  // Validate email
  const emailRegex = /^[a-zA-Z0-9._%+-]+@gmail\.com$/;
  if (!emailRegex.test(email)) {
    return res.status(400).json({ success: false, error: "Invalid email format." });
  }

  // Validate password strength
  const passwordRegex = /^(?=.*[A-Z])(?=.*[a-z])(?=.*\d)(?=.*[!@#$%^&*]).{8,}$/;
  if (!passwordRegex.test(password)) {
    return res.status(400).json({ success: false, error: "Weak password." });
  }

  try {
    // Check if email already exists
    db.get(`SELECT * FROM user WHERE email = ?`, [email], async (err, row) => {
      if (err) {
        console.error("❌ DB error:", err);
        return res.status(500).json({ success: false, error: "Database error." });
      }

      if (row) {
        return res.status(409).json({ success: false, error: "Email already registered." });
      }

      // Hash password and insert
      const hashedPassword = await bcrypt.hash(password, SALT_ROUNDS);

      db.run(`
        INSERT INTO user (first_name, last_name, email, password)
        VALUES (?, ?, ?, ?)
      `, [first_name, last_name, email, hashedPassword], function (err) {
        if (err) {
          console.error("❌ Insert error:", err);
          return res.status(500).json({ success: false, error: "Could not register user." });
        }

        console.log(`✅ User registered: ${email}`);
        req.session.user_email = email;
        return res.json({ success: true, message: "User registered successfully." });
      });
    });
  } catch (err) {
    console.error("❌ Unexpected error:", err);
    return res.status(500).json({ success: false, error: "Unexpected error." });
  }
});

app.get('/api/get-user', (req, res) => {
  const userEmail = req.session.user_email || null;
  res.json({ user_email: userEmail });
});

app.get('/logout', (req, res) => {
  req.session.destroy(err => {
    if (err) {
      return res.status(500).send("Logout failed");
    }
    res.redirect('/index.html'); // 👈 Redirect to homepage instead
  });
});

app.post('/api/login', (req, res) => {
  const { email, password } = req.body;

  if (!email || !password) {
    return res.status(400).json({ success: false, error: "Missing email or password." });
  }

  db.get(`SELECT * FROM user WHERE email = ?`, [email], async (err, user) => {
    if (err) {
      console.error("❌ DB error:", err);
      return res.status(500).json({ success: false });
    }

    if (!user) {
      return res.status(404).json({ success: false, error: "Email not found." });
    }

    const match = await bcrypt.compare(password, user.password);
    if (!match) {
      return res.status(401).json({ success: false, error: "Incorrect password." });
    }

    req.session.user_email = user.email;
    return res.json({ success: true });
  });
});


app.get('/api/check_email', (req, res) => {
  const email = req.query.email;
  if (!email) return res.json({ exists: false });

  db.get(`SELECT * FROM user WHERE email = ?`, [email], (err, row) => {
    if (err) return res.json({ exists: false });
    return res.json({ exists: !!row });
  });
});

app.get('/auth/google',
  passport.authenticate('google', { scope: ['profile', 'email'] })
);

app.get('/auth/google/callback',
  passport.authenticate('google', { failureRedirect: '/login.html' }),
  (req, res) => {
    req.session.user_email = req.user.email;
    res.redirect('/dashboard');
  }
);

app.post('/api/report-ready', (req, res) => {
  const { reportFile } = req.body;
  if (!reportFile) {
    console.error("❌ Missing reportFile in request.");
    return res.status(400).json({ success: false, error: "reportFile missing." });
  }

  console.log("📨 Report ready:", reportFile);

  // Broadcast to all connected WebSocket clients
  wss.clients.forEach(client => {
    if (client.readyState === WebSocket.OPEN) {
      client.send(JSON.stringify({
        type: "reportReady",
        reportFile: reportFile
      }));
    }
  });

  res.status(200).json({ success: true, message: "Clients notified." });
});




