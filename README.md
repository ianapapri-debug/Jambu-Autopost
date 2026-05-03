# 🍃 Jambu Store Autopost

Dashboard autopost Discord modern berbasis Flask.

---

## 📁 Struktur Folder

```
jambu-store/
├── app.py                  ← Entry point utama
├── requirements.txt        ← Dependencies Python
├── instance/
│   └── jambu.db            ← Database SQLite (dibuat otomatis)
├── static/
│   ├── css/
│   │   └── style.css
│   └── js/
│       └── main.js
└── templates/
    ├── base.html           ← Layout utama (sidebar, topbar, toast)
    ├── login.html          ← Halaman login
    ├── dashboard.html      ← Dashboard admin
    ├── channels.html       ← Kelola channel autopost
    ├── settings.html       ← Token & Webhook
    ├── logs.html           ← Activity Log
    └── users.html          ← User Management
```

---

## 🚀 Cara Menjalankan

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Jalankan server
```bash
python app.py
```

### 3. Buka browser
```
http://localhost:5000
```

### 4. Login default
```
Username : admin
Password : admin123
```

---

## 🗃️ Database Schema

### Tabel `users`
| Kolom       | Tipe    | Keterangan           |
|-------------|---------|----------------------|
| id          | INTEGER | Primary Key          |
| username    | TEXT    | Unique, not null     |
| password    | TEXT    | SHA-256 hash         |
| role        | TEXT    | 'admin' atau 'user'  |
| created_at  | TEXT    | Waktu pembuatan      |

### Tabel `tokens`
| Kolom       | Tipe    | Keterangan           |
|-------------|---------|----------------------|
| id          | INTEGER | Primary Key          |
| user_id     | INTEGER | FK → users.id        |
| name        | TEXT    | Nama token           |
| token       | TEXT    | Discord token        |
| created_at  | TEXT    | Waktu pembuatan      |

### Tabel `channels`
| Kolom       | Tipe    | Keterangan              |
|-------------|---------|-------------------------|
| id          | INTEGER | Primary Key             |
| user_id     | INTEGER | FK → users.id           |
| channel_id  | TEXT    | ID channel Discord      |
| interval    | INTEGER | Interval detik (≥ 60)   |
| message     | TEXT    | Pesan yang dikirim      |
| is_active   | INTEGER | 0=stop, 1=aktif         |
| last_sent   | TEXT    | Waktu kirim terakhir    |
| created_at  | TEXT    | Waktu pembuatan         |

### Tabel `logs`
| Kolom       | Tipe    | Keterangan           |
|-------------|---------|----------------------|
| id          | INTEGER | Primary Key          |
| user_id     | INTEGER | FK → users.id        |
| username    | TEXT    | Username pelaku      |
| action      | TEXT    | Jenis aksi           |
| detail      | TEXT    | Detail aksi          |
| created_at  | TEXT    | Waktu aksi           |

### Tabel `settings`
| Kolom       | Tipe    | Keterangan           |
|-------------|---------|----------------------|
| id          | INTEGER | Primary Key          |
| user_id     | INTEGER | FK → users.id (UNIQUE)|
| webhook_url | TEXT    | Discord Webhook URL  |

---

## 📡 Contoh Request Discord API

### Kirim Pesan ke Channel
```
POST https://discord.com/api/v9/channels/{channel_id}/messages
Authorization: Bot YOUR_TOKEN_HERE
Content-Type: application/json

{
  "content": "Pesan autopost dari Jambu Store!"
}
```

### Kirim Embed ke Webhook
```
POST https://discord.com/api/webhooks/{webhook_id}/{webhook_token}
Content-Type: application/json

{
  "embeds": [{
    "title": "📨 Autopost Report",
    "color": 5046077,
    "fields": [
      { "name": "Token", "value": "Bot Utama", "inline": true },
      { "name": "Status", "value": "SUCCESS", "inline": true },
      { "name": "Channel ID", "value": "1234567890", "inline": true },
      { "name": "Waktu", "value": "2025-01-01 12:00:00", "inline": false }
    ]
  }]
}
```

---

## ✅ Fitur Lengkap

- [x] Login & Session Auth
- [x] Role Admin & User
- [x] Dashboard dengan statistik
- [x] Add channel single & bulk (hingga 100)
- [x] Edit, delete, start/stop channel
- [x] Scheduler autopost berbasis threading
- [x] Token CRUD
- [x] Webhook notifikasi embed
- [x] Activity Log
- [x] User Management (CRUD)
- [x] Toast notification
- [x] Responsive mobile
- [x] Dark mode modern

---

## 🔐 Catatan Keamanan

- Password di-hash menggunakan SHA-256
- Session berbasis Flask secret key (random setiap restart)
- Untuk produksi: gunakan `SECRET_KEY` yang tetap di environment variable
- Untuk produksi: gunakan Gunicorn/uWSGI + Nginx
