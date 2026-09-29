# JTS PowerTool - Enterprise Slack Automation & API Management Platform

> **A production-grade full-stack solution for Slack workspace automation, API management, token management, and secret vault operations**

---

## Overview

JTS PowerTool is a comprehensive **Slack automation and API management platform** built with modern technologies. It provides enterprises with complete control over multi-workspace Slack bot operations, secure API key management, usage tracking, and comprehensive admin dashboards.

###  Key Features

**Multi-Workspace Slack Bot Integration**
- Seamless automation across multiple Slack workspaces
- Dynamic token discovery with fallback mechanisms
- Bot command handling and event processing
- Real-time channel telemetry and monitoring

 **RESTful API Ecosystem**
- User management and role-based access control (RBAC)
- Organization management with hierarchical structure
- API approval workflows
- Comprehensive logging and audit trails

 **Secure Secret Management**
- Encrypted API keys vault with organization isolation
- Secure credential storage and retrieval
- Token usage tracking and cost attribution
- Channel secrets management for Slack integrations

 **Production-Ready Infrastructure**
- Docker containerization with Nginx reverse proxy
- PostgreSQL database with Alembic migrations
- SystemD service management for process control
- Multi-environment configuration support

 **Comprehensive Dashboard**
- Admin dashboard with analytics and reporting
- Client-facing dashboard with billing and usage insights
- User management interface with org assignment
- Profile settings and preferences management

 **Advanced Features**
- File generation and extraction tools
- Context and conversation management
- Log streaming and real-time monitoring
- Memory management for conversation history
- Web search and URL content fetching

---

## Architecture

### Technology Stack

| Layer | Technology |
|-------|-----------|
| **Backend API** | Python FastAPI |
| **Database** | PostgreSQL with Alembic migrations |
| **Frontend** | React with responsive UI |
| **Server** | Nginx reverse proxy |
| **Containerization** | Docker & Docker Compose |
| **Service Management** | SystemD |
| **Integrations** | Slack API, AWS Secrets Manager |

### Project Structure

```
jts-powertool/
├── app/                          # Backend API (FastAPI)
│   ├── main.py                  # Application entry point
│   ├── auth_router.py           # Authentication & JWT tokens
│   ├── db_router.py             # Database & user operations
│   ├── slack_router.py          # Slack bot & workspace management
│   ├── organizations_router.py  # Organization management
│   ├── channel_secrets_router.py# Secret vault operations
│   ├── api_approvals.py         # Approval workflow logic
│   ├── memory_manager.py        # Conversation memory management
│   ├── worker.py                # Background tasks & automation
│   ├── claude.py                # Claude AI integration
│   ├── file_generator.py        # Document generation (PDF, Word, Excel)
│   ├── file_extractor.py        # File parsing & extraction
│   ├── log_stream.py            # Real-time log streaming
│   ├── vector_memory.py         # Vector database for memory
│   ├── db/                      # Database models & schemas
│   ├── services/                # Business logic services
│   ├── tools/                   # Reusable tool utilities
│   └── teams/                   # Multi-workspace team support
├── frontend/                     # React dashboard
├── alembic/                      # Database migrations
├── nginx/                        # Nginx configuration
├── systemd/                      # SystemD service files
├── init_db.sql                   # Database initialization
├── requirements.txt              # Python dependencies
├── alembic.ini                   # Alembic configuration
└── docker-compose.yml            # Container orchestration
```

---

## Getting Started

### Prerequisites

- Python 3.8+
- PostgreSQL 12+
- Docker & Docker Compose
- Node.js 14+ (for frontend)

### Installation

1. **Clone the repository**
```bash
git clone https://github.com/AbdulAleemDev/jts-powertool.git
cd jts-powertool
```

2. **Set up environment variables**
```bash
cp .env.example .env
# Edit .env with your configuration
```

3. **Install dependencies**
```bash
pip install -r requirements.txt
```

4. **Initialize database**
```bash
alembic upgrade head
psql -U postgres -d jts_powertool -f init_db.sql
```

5. **Run the application**
```bash
# Development
python -m uvicorn app.main:app --reload

# Production with Gunicorn
gunicorn app.main:app -w 4 -b 0.0.0.0:8000
```

---

## API Endpoints

### Authentication
- `POST /api/auth/register` - User registration
- `POST /api/auth/login` - User login with JWT
- `POST /api/auth/refresh` - Refresh JWT token
- `POST /api/auth/logout` - User logout

### User Management
- `GET /api/users` - List all users with pagination
- `POST /api/users` - Create new user
- `GET /api/users/{user_id}` - Get user details
- `PUT /api/users/{user_id}` - Update user information
- `PATCH /api/users/{user_id}` - Partial user update
- `DELETE /api/users/{user_id}` - Delete user

### Organization Management
- `GET /api/organizations` - List organizations
- `POST /api/organizations` - Create organization
- `GET /api/organizations/{org_id}` - Get organization details
- `PUT /api/organizations/{org_id}` - Update organization
- `DELETE /api/organizations/{org_id}` - Delete organization

### Secret Vault (API Keys)
- `GET /api/vault/keys` - List API keys with organization isolation
- `POST /api/vault/keys` - Create encrypted API key
- `GET /api/vault/keys/{key_id}` - Retrieve decrypted key value
- `DELETE /api/vault/keys/{key_id}` - Remove API key

### Slack Integration
- `POST /api/slack/authorize` - Authorize Slack workspace
- `GET /api/slack/workspaces` - List connected workspaces
- `POST /api/slack/events` - Handle Slack events
- `POST /api/slack/commands` - Process slash commands

### Usage & Billing
- `GET /api/usage/tokens` - Token usage statistics
- `GET /api/usage/costs` - Cost attribution per channel/workspace
- `GET /api/usage/channels` - Channel telemetry data

### Logs & Monitoring
- `GET /api/logs` - Retrieve audit logs
- `WS /api/logs/stream` - WebSocket real-time log streaming

---

##  Security Features

**JWT Authentication** - Secure token-based authentication
**Role-Based Access Control (RBAC)** - Admin, Client Admin, Client User roles
**Encrypted Secret Storage** - AES encryption for API keys
**AWS Secrets Manager Integration** - Centralized secret management
**Transaction Management** - ACID compliance for data integrity
**Audit Logging** - Complete activity tracking
**Environment Isolation** - Separate dev, staging, production configs
**CORS & Security Headers** - Protection against common vulnerabilities

---

## Database Schema

### Core Tables
- **users** - User accounts with roles and permissions
- **organizations** - Enterprise organization units
- **slack_workspaces** - Connected Slack workspace metadata
- **api_vault_keys** - Encrypted API credentials storage
- **api_approvals** - Approval workflow tracking
- **channel_telemetry** - Slack channel usage analytics
- **conversation_messages** - Message history with token usage
- **token_usage** - Token consumption tracking for billing
- **audit_logs** - Complete audit trail of all operations

---

## Database Migrations

Using Alembic for schema management:

```bash
# Create new migration
alembic revision --autogenerate -m "Add new feature"

# Apply migrations
alembic upgrade head

# Rollback migration
alembic downgrade -1

# View current version
alembic current
```

---

## Deployment

### Docker Deployment

```bash
# Build and run with Docker Compose
docker-compose build
docker-compose up -d

# View logs
docker-compose logs -f app

# Stop services
docker-compose down
```

### Systemd Service Management

```bash
# Copy service file
sudo cp systemd/jts-powertool.service /etc/systemd/system/

# Enable and start service
sudo systemctl enable jts-powertool
sudo systemctl start jts-powertool

# Check status
sudo systemctl status jts-powertool

# View logs
sudo journalctl -u jts-powertool -f
```

### Nginx Configuration

```bash
# Copy Nginx config
sudo cp nginx/jts-powertool.conf /etc/nginx/sites-available/
sudo ln -s /etc/nginx/sites-available/jts-powertool.conf /etc/nginx/sites-enabled/

# Test configuration
sudo nginx -t

# Reload Nginx
sudo systemctl reload nginx
```

---

##  Configuration

### Environment Variables

```env
# Database
DATABASE_URL=postgresql://user:password@localhost/jts_powertool
SQLALCHEMY_DATABASE_URL=postgresql://user:password@localhost/jts_powertool

# JWT Security
SECRET_KEY=your-super-secret-key-change-this-in-production
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30

# Slack
SLACK_BOT_TOKEN=xoxb-your-token
SLACK_APP_TOKEN=xapp-your-token
SLACK_SIGNING_SECRET=your-signing-secret

# AWS
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key

# Application
DEBUG=false
LOG_LEVEL=INFO
```

---

##  Documentation

- **[User Guide](JTS_PowerTool_Dashboard_User_Guide.pdf)** - Dashboard and feature documentation
- **[Developer Guide](JTS_PowerTool_Developer_Architecture_Guide.pdf)** - Complete architecture and development reference

---

##  Testing

```bash
# Run unit tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=app

# Test specific module
pytest tests/test_auth.py -v
```

---

##  Recent Improvements

### Latest Features
- Live web search and URL content fetching tools
- Dynamic date and timezone localization in Slack messages
- Multi-workspace bot token and mention resolution
- Profile settings page for admin and client dashboards
- Organization dropdown assignment for Client Admin
- Full Name column in API Keys Vault
- User creation with name field and email uniqueness

### Bug Fixes & Optimizations
- Transaction abort resolution in user list operations
- Support for PUT, PATCH, POST methods in user updates
- Channel ID corruption fixes for Slack channel mapping
- Token usage and cost attribution accuracy improvements

---

##  Multi-Workspace Support

JTS PowerTool is built with **multi-workspace Slack automation** at its core:

**Dynamic Token Discovery** - Automatically resolves bot tokens across workspaces
**Workspace Isolation** - Organization data is workspace-specific
**Fallback Mechanisms** - Graceful handling of missing workspace tokens
**Centralized Management** - Single dashboard for all workspace operations
**Event Processing** - Handle Slack events from multiple workspaces simultaneously

---

## Use Cases

### For Enterprises
- Centralized Slack workspace management across departments
- Automated channel provisioning and management
- Secure API credential distribution and rotation
- Usage tracking and cost optimization

### For Agencies & SaaS Providers
- Multi-client workspace management
- White-label dashboard for clients
- Usage-based billing and cost allocation
- Compliance and audit reporting

### For Development Teams
- Automated workflow integration
- Channel analytics and telemetry
- CI/CD pipeline integration
- Log aggregation and monitoring

---

##  Contributing

Contributions are welcome! Please follow these steps:

1. Create a feature branch (`git checkout -b feature/amazing-feature`)
2. Commit your changes (`git commit -m 'Add amazing feature'`)
3. Push to the branch (`git push origin feature/amazing-feature`)
4. Open a Pull Request

---

##  License

This project is licensed under the MIT License - see the LICENSE file for details.

---

##  Support & Contact

- **Issues** - Report bugs and request features on [GitHub Issues](https://github.com/AbdulAleemDev/jts-powertool/issues)
- **Author** - Abdul Aleem [@AbdulAleemDev](https://github.com/AbdulAleemDev)
- **Email** - aleemman1234@gmail.com

---

##  Acknowledgments

- FastAPI framework for building modern REST APIs
- PostgreSQL for reliable data storage
- Slack API for workspace integration
- React for the beautiful dashboard UI
- AWS for cloud infrastructure services

---

<div align="center">

** If you found this project helpful, please consider giving it a star!**

Made by Abdul Aleem

</div>
