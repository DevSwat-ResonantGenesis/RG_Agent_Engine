"""
Template Engine - Project Scaffolding
======================================

Generates project scaffolding for different tech stacks.

Features:
- React + TypeScript frontend templates
- FastAPI backend templates
- Full-stack project generation
- Dependency management
- Configuration files
"""

import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class ProjectType(str, Enum):
    """Types of projects."""
    FRONTEND_REACT = "frontend_react"
    FRONTEND_VUE = "frontend_vue"
    BACKEND_FASTAPI = "backend_fastapi"
    BACKEND_EXPRESS = "backend_express"
    FULLSTACK_REACT_FASTAPI = "fullstack_react_fastapi"
    FULLSTACK_REACT_EXPRESS = "fullstack_react_express"


@dataclass
class FileTemplate:
    """A file template."""
    path: str
    content: str
    is_binary: bool = False


@dataclass
class ProjectTemplate:
    """A project template."""
    project_type: ProjectType
    name: str
    description: str
    files: List[FileTemplate] = field(default_factory=list)
    dependencies: Dict[str, str] = field(default_factory=dict)
    dev_dependencies: Dict[str, str] = field(default_factory=dict)
    scripts: Dict[str, str] = field(default_factory=dict)


class TemplateEngine:
    """
    Generates project scaffolding from templates.
    """
    
    def __init__(self):
        self._templates: Dict[ProjectType, ProjectTemplate] = {}
        self._load_templates()
        logger.info("TemplateEngine initialized")
    
    def _load_templates(self):
        """Load all project templates."""
        self._templates[ProjectType.FULLSTACK_REACT_FASTAPI] = self._create_fullstack_template()
        self._templates[ProjectType.FRONTEND_REACT] = self._create_react_template()
        self._templates[ProjectType.BACKEND_FASTAPI] = self._create_fastapi_template()
    
    def _create_fullstack_template(self) -> ProjectTemplate:
        """Create fullstack React + FastAPI template."""
        return ProjectTemplate(
            project_type=ProjectType.FULLSTACK_REACT_FASTAPI,
            name="Fullstack React + FastAPI",
            description="Modern fullstack application with React frontend and FastAPI backend",
            files=[
                FileTemplate(
                    path="frontend/package.json",
                    content='''{
  "name": "{{project_name}}-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "lint": "eslint . --ext ts,tsx --report-unused-disable-directives --max-warnings 0",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    "react-router-dom": "^6.20.0",
    "@tanstack/react-query": "^5.8.0",
    "axios": "^1.6.0",
    "lucide-react": "^0.294.0",
    "clsx": "^2.0.0",
    "tailwind-merge": "^2.0.0"
  },
  "devDependencies": {
    "@types/react": "^18.2.37",
    "@types/react-dom": "^18.2.15",
    "@typescript-eslint/eslint-plugin": "^6.10.0",
    "@typescript-eslint/parser": "^6.10.0",
    "@vitejs/plugin-react": "^4.2.0",
    "autoprefixer": "^10.4.16",
    "eslint": "^8.53.0",
    "eslint-plugin-react-hooks": "^4.6.0",
    "eslint-plugin-react-refresh": "^0.4.4",
    "postcss": "^8.4.31",
    "tailwindcss": "^3.3.5",
    "typescript": "^5.2.2",
    "vite": "^5.0.0"
  }
}''',
                ),
                FileTemplate(
                    path="frontend/tsconfig.json",
                    content='''{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "baseUrl": ".",
    "paths": {
      "@/*": ["src/*"]
    }
  },
  "include": ["src"],
  "references": [{ "path": "./tsconfig.node.json" }]
}''',
                ),
                FileTemplate(
                    path="frontend/tsconfig.node.json",
                    content='''{
  "compilerOptions": {
    "composite": true,
    "skipLibCheck": true,
    "module": "ESNext",
    "moduleResolution": "bundler",
    "allowSyntheticDefaultImports": true
  },
  "include": ["vite.config.ts"]
}''',
                ),
                FileTemplate(
                    path="frontend/vite.config.ts",
                    content='''import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})''',
                ),
                FileTemplate(
                    path="frontend/tailwind.config.js",
                    content='''/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {},
  },
  plugins: [],
}''',
                ),
                FileTemplate(
                    path="frontend/postcss.config.js",
                    content='''export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
}''',
                ),
                FileTemplate(
                    path="frontend/index.html",
                    content='''<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <link rel="icon" type="image/svg+xml" href="/vite.svg" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{{project_name}}</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>''',
                ),
                FileTemplate(
                    path="frontend/src/main.tsx",
                    content='''import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'
import './index.css'

const queryClient = new QueryClient()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </React.StrictMode>,
)''',
                ),
                FileTemplate(
                    path="frontend/src/App.tsx",
                    content='''import { BrowserRouter, Routes, Route } from 'react-router-dom'
import MainLayout from './components/Layout/MainLayout'
import Dashboard from './pages/Dashboard'

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<MainLayout />}>
          <Route index element={<Dashboard />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

export default App''',
                ),
                FileTemplate(
                    path="frontend/src/index.css",
                    content='''@tailwind base;
@tailwind components;
@tailwind utilities;

:root {
  font-family: Inter, system-ui, Avenir, Helvetica, Arial, sans-serif;
  line-height: 1.5;
  font-weight: 400;
}

body {
  margin: 0;
  min-width: 320px;
  min-height: 100vh;
}''',
                ),
                FileTemplate(
                    path="frontend/src/components/Layout/MainLayout.tsx",
                    content='''import { Outlet } from 'react-router-dom'
import Sidebar from './Sidebar'
import Header from './Header'

export default function MainLayout() {
  return (
    <div className="flex h-screen bg-gray-100">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-x-hidden overflow-y-auto bg-gray-100 p-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}''',
                ),
                FileTemplate(
                    path="frontend/src/components/Layout/Sidebar.tsx",
                    content='''import { Home, Users, ShoppingCart, Calendar, BarChart3 } from 'lucide-react'
import { Link, useLocation } from 'react-router-dom'
import { clsx } from 'clsx'

const navigation = [
  { name: 'Dashboard', href: '/', icon: Home },
  { name: 'Customers', href: '/customers', icon: Users },
  { name: 'Orders', href: '/orders', icon: ShoppingCart },
  { name: 'Reservations', href: '/reservations', icon: Calendar },
  { name: 'Analytics', href: '/analytics', icon: BarChart3 },
]

export default function Sidebar() {
  const location = useLocation()

  return (
    <div className="hidden md:flex md:w-64 md:flex-col">
      <div className="flex flex-col flex-grow pt-5 bg-indigo-700 overflow-y-auto">
        <div className="flex items-center flex-shrink-0 px-4">
          <span className="text-white text-xl font-bold">{{project_name}}</span>
        </div>
        <div className="mt-5 flex-1 flex flex-col">
          <nav className="flex-1 px-2 pb-4 space-y-1">
            {navigation.map((item) => {
              const isActive = location.pathname === item.href
              return (
                <Link
                  key={item.name}
                  to={item.href}
                  className={clsx(
                    isActive
                      ? 'bg-indigo-800 text-white'
                      : 'text-indigo-100 hover:bg-indigo-600',
                    'group flex items-center px-2 py-2 text-sm font-medium rounded-md'
                  )}
                >
                  <item.icon className="mr-3 h-5 w-5" />
                  {item.name}
                </Link>
              )
            })}
          </nav>
        </div>
      </div>
    </div>
  )
}''',
                ),
                FileTemplate(
                    path="frontend/src/components/Layout/Header.tsx",
                    content='''import { Bell, Search, User } from 'lucide-react'

export default function Header() {
  return (
    <header className="bg-white shadow-sm">
      <div className="flex items-center justify-between px-6 py-4">
        <div className="flex items-center">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 h-5 w-5 text-gray-400" />
            <input
              type="text"
              placeholder="Search..."
              className="pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>
        </div>
        <div className="flex items-center space-x-4">
          <button className="p-2 text-gray-400 hover:text-gray-500">
            <Bell className="h-6 w-6" />
          </button>
          <button className="p-2 text-gray-400 hover:text-gray-500">
            <User className="h-6 w-6" />
          </button>
        </div>
      </div>
    </header>
  )
}''',
                ),
                FileTemplate(
                    path="frontend/src/pages/Dashboard.tsx",
                    content='''import { useQuery } from '@tanstack/react-query'
import { TrendingUp, Users, ShoppingCart, DollarSign } from 'lucide-react'
import { api } from '../api/client'

const stats = [
  { name: 'Total Revenue', value: '$45,231', icon: DollarSign, change: '+20.1%' },
  { name: 'Customers', value: '2,338', icon: Users, change: '+15.2%' },
  { name: 'Orders', value: '1,234', icon: ShoppingCart, change: '+12.5%' },
  { name: 'Growth', value: '23%', icon: TrendingUp, change: '+4.3%' },
]

export default function Dashboard() {
  const { data: dashboardData, isLoading } = useQuery({
    queryKey: ['dashboard'],
    queryFn: () => api.get('/api/v1/dashboard').then(res => res.data),
  })

  return (
    <div>
      <h1 className="text-2xl font-semibold text-gray-900">Dashboard</h1>
      
      <div className="mt-6 grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-4">
        {stats.map((stat) => (
          <div key={stat.name} className="bg-white overflow-hidden shadow rounded-lg">
            <div className="p-5">
              <div className="flex items-center">
                <div className="flex-shrink-0">
                  <stat.icon className="h-6 w-6 text-gray-400" />
                </div>
                <div className="ml-5 w-0 flex-1">
                  <dl>
                    <dt className="text-sm font-medium text-gray-500 truncate">{stat.name}</dt>
                    <dd className="flex items-baseline">
                      <div className="text-2xl font-semibold text-gray-900">{stat.value}</div>
                      <div className="ml-2 flex items-baseline text-sm font-semibold text-green-600">
                        {stat.change}
                      </div>
                    </dd>
                  </dl>
                </div>
              </div>
            </div>
          </div>
        ))}
      </div>
      
      <div className="mt-8 grid grid-cols-1 gap-6 lg:grid-cols-2">
        <div className="bg-white shadow rounded-lg p-6">
          <h2 className="text-lg font-medium text-gray-900">Recent Orders</h2>
          <p className="mt-2 text-gray-500">Loading orders...</p>
        </div>
        <div className="bg-white shadow rounded-lg p-6">
          <h2 className="text-lg font-medium text-gray-900">Upcoming Reservations</h2>
          <p className="mt-2 text-gray-500">Loading reservations...</p>
        </div>
      </div>
    </div>
  )
}''',
                ),
                FileTemplate(
                    path="frontend/src/api/client.ts",
                    content='''import axios from 'axios'

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || '',
  headers: {
    'Content-Type': 'application/json',
  },
})

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('token')
      window.location.href = '/login'
    }
    return Promise.reject(error)
  }
)''',
                ),
                FileTemplate(
                    path="frontend/src/vite-env.d.ts",
                    content='''/// <reference types="vite/client" />''',
                ),
                FileTemplate(
                    path="backend/requirements.txt",
                    content='''fastapi==0.104.1
uvicorn[standard]==0.24.0
sqlalchemy==2.0.23
alembic==1.12.1
pydantic==2.5.2
pydantic-settings==2.1.0
python-jose[cryptography]==3.3.0
passlib[bcrypt]==1.7.4
python-multipart==0.0.6
httpx==0.25.2
asyncpg==0.29.0
redis==5.0.1
''',
                ),
                FileTemplate(
                    path="backend/app/__init__.py",
                    content='"""{{project_name}} Backend"""',
                ),
                FileTemplate(
                    path="backend/app/main.py",
                    content='''"""
{{project_name}} API
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import customers, orders, reservations, staff, analytics
from app.core.config import settings

app = FastAPI(
    title="{{project_name}} API",
    description="Backend API for {{project_name}}",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(customers.router, prefix="/api/v1/customers", tags=["customers"])
app.include_router(orders.router, prefix="/api/v1/orders", tags=["orders"])
app.include_router(reservations.router, prefix="/api/v1/reservations", tags=["reservations"])
app.include_router(staff.router, prefix="/api/v1/staff", tags=["staff"])
app.include_router(analytics.router, prefix="/api/v1/analytics", tags=["analytics"])


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "{{project_name}}"}


@app.get("/api/v1/dashboard")
async def get_dashboard():
    return {
        "total_revenue": 45231,
        "total_customers": 2338,
        "total_orders": 1234,
        "growth_rate": 23,
    }
''',
                ),
                FileTemplate(
                    path="backend/app/core/__init__.py",
                    content='"""Core module"""',
                ),
                FileTemplate(
                    path="backend/app/core/config.py",
                    content='''from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    PROJECT_NAME: str = "{{project_name}}"
    DEBUG: bool = True
    
    DATABASE_URL: str = "postgresql+asyncpg://user:password@localhost:5432/{{project_name}}"
    REDIS_URL: str = "redis://localhost:6379"
    
    SECRET_KEY: str = "your-secret-key-change-in-production"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    
    CORS_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:5173"]
    
    class Config:
        env_file = ".env"


settings = Settings()
''',
                ),
                FileTemplate(
                    path="backend/app/routers/__init__.py",
                    content='"""API Routers"""',
                ),
                FileTemplate(
                    path="backend/app/routers/customers.py",
                    content='''from fastapi import APIRouter, HTTPException
from typing import List
from pydantic import BaseModel

router = APIRouter()


class Customer(BaseModel):
    id: int
    name: str
    email: str
    phone: str


class CustomerCreate(BaseModel):
    name: str
    email: str
    phone: str


@router.get("/", response_model=List[Customer])
async def list_customers():
    return []


@router.get("/{customer_id}", response_model=Customer)
async def get_customer(customer_id: int):
    raise HTTPException(status_code=404, detail="Customer not found")


@router.post("/", response_model=Customer)
async def create_customer(customer: CustomerCreate):
    return Customer(id=1, **customer.model_dump())


@router.put("/{customer_id}", response_model=Customer)
async def update_customer(customer_id: int, customer: CustomerCreate):
    return Customer(id=customer_id, **customer.model_dump())


@router.delete("/{customer_id}")
async def delete_customer(customer_id: int):
    return {"message": "Customer deleted"}
''',
                ),
                FileTemplate(
                    path="backend/app/routers/orders.py",
                    content='''from fastapi import APIRouter, HTTPException
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime

router = APIRouter()


class OrderItem(BaseModel):
    name: str
    quantity: int
    price: float


class Order(BaseModel):
    id: int
    customer_id: int
    items: List[OrderItem]
    total: float
    status: str
    created_at: datetime


class OrderCreate(BaseModel):
    customer_id: int
    items: List[OrderItem]


@router.get("/", response_model=List[Order])
async def list_orders(status: Optional[str] = None):
    return []


@router.get("/{order_id}", response_model=Order)
async def get_order(order_id: int):
    raise HTTPException(status_code=404, detail="Order not found")


@router.post("/", response_model=Order)
async def create_order(order: OrderCreate):
    total = sum(item.price * item.quantity for item in order.items)
    return Order(
        id=1,
        customer_id=order.customer_id,
        items=order.items,
        total=total,
        status="pending",
        created_at=datetime.utcnow(),
    )


@router.put("/{order_id}/status")
async def update_order_status(order_id: int, status: str):
    return {"message": f"Order {order_id} status updated to {status}"}
''',
                ),
                FileTemplate(
                    path="backend/app/routers/reservations.py",
                    content='''from fastapi import APIRouter, HTTPException
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime

router = APIRouter()


class Reservation(BaseModel):
    id: int
    customer_name: str
    customer_phone: str
    party_size: int
    date: datetime
    status: str


class ReservationCreate(BaseModel):
    customer_name: str
    customer_phone: str
    party_size: int
    date: datetime


@router.get("/", response_model=List[Reservation])
async def list_reservations(date: Optional[str] = None):
    return []


@router.get("/{reservation_id}", response_model=Reservation)
async def get_reservation(reservation_id: int):
    raise HTTPException(status_code=404, detail="Reservation not found")


@router.post("/", response_model=Reservation)
async def create_reservation(reservation: ReservationCreate):
    return Reservation(id=1, status="confirmed", **reservation.model_dump())


@router.put("/{reservation_id}/cancel")
async def cancel_reservation(reservation_id: int):
    return {"message": f"Reservation {reservation_id} cancelled"}
''',
                ),
                FileTemplate(
                    path="backend/app/routers/staff.py",
                    content='''from fastapi import APIRouter, HTTPException
from typing import List
from pydantic import BaseModel

router = APIRouter()


class Staff(BaseModel):
    id: int
    name: str
    role: str
    email: str


class StaffCreate(BaseModel):
    name: str
    role: str
    email: str


@router.get("/", response_model=List[Staff])
async def list_staff():
    return []


@router.get("/{staff_id}", response_model=Staff)
async def get_staff(staff_id: int):
    raise HTTPException(status_code=404, detail="Staff not found")


@router.post("/", response_model=Staff)
async def create_staff(staff: StaffCreate):
    return Staff(id=1, **staff.model_dump())
''',
                ),
                FileTemplate(
                    path="backend/app/routers/analytics.py",
                    content='''from fastapi import APIRouter
from typing import Dict, Any
from datetime import datetime, timedelta

router = APIRouter()


@router.get("/revenue")
async def get_revenue_analytics() -> Dict[str, Any]:
    return {
        "total": 45231,
        "daily": [
            {"date": (datetime.utcnow() - timedelta(days=i)).isoformat(), "amount": 1500 + i * 100}
            for i in range(7)
        ],
    }


@router.get("/orders")
async def get_order_analytics() -> Dict[str, Any]:
    return {
        "total": 1234,
        "by_status": {
            "pending": 45,
            "preparing": 23,
            "ready": 12,
            "completed": 1154,
        },
    }


@router.get("/customers")
async def get_customer_analytics() -> Dict[str, Any]:
    return {
        "total": 2338,
        "new_this_month": 156,
        "returning_rate": 0.68,
    }
''',
                ),
                FileTemplate(
                    path="docker-compose.yml",
                    content='''version: '3.8'

services:
  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile
    ports:
      - "3000:3000"
    environment:
      - VITE_API_URL=http://localhost:8000
    depends_on:
      - backend

  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql+asyncpg://postgres:postgres@db:5432/{{project_name}}
      - REDIS_URL=redis://redis:6379
    depends_on:
      - db
      - redis

  db:
    image: postgres:15
    environment:
      - POSTGRES_USER=postgres
      - POSTGRES_PASSWORD=postgres
      - POSTGRES_DB={{project_name}}
    volumes:
      - postgres_data:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    volumes:
      - redis_data:/data

volumes:
  postgres_data:
  redis_data:
''',
                ),
                FileTemplate(
                    path="README.md",
                    content='''# {{project_name}}

A modern fullstack application built with React and FastAPI.

## Tech Stack

### Frontend
- React 18 with TypeScript
- Vite for build tooling
- TailwindCSS for styling
- React Query for data fetching
- React Router for navigation

### Backend
- FastAPI
- SQLAlchemy with async support
- PostgreSQL database
- Redis for caching

## Getting Started

### Prerequisites
- Node.js 18+
- Python 3.11+
- Docker (optional)

### Development

#### Frontend
```bash
cd frontend
npm install
npm run dev
```

#### Backend
```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### Docker
```bash
docker-compose up -d
```

## API Documentation

Once running, visit:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
''',
                ),
            ],
        )
    
    def _create_react_template(self) -> ProjectTemplate:
        """Create React-only template."""
        fullstack = self._create_fullstack_template()
        return ProjectTemplate(
            project_type=ProjectType.FRONTEND_REACT,
            name="React Frontend",
            description="Modern React frontend with TypeScript and TailwindCSS",
            files=[f for f in fullstack.files if f.path.startswith("frontend/")],
        )
    
    def _create_fastapi_template(self) -> ProjectTemplate:
        """Create FastAPI-only template."""
        fullstack = self._create_fullstack_template()
        return ProjectTemplate(
            project_type=ProjectType.BACKEND_FASTAPI,
            name="FastAPI Backend",
            description="FastAPI backend with SQLAlchemy and async support",
            files=[f for f in fullstack.files if f.path.startswith("backend/")],
        )
    
    def get_template(self, project_type: ProjectType) -> Optional[ProjectTemplate]:
        """Get a project template."""
        return self._templates.get(project_type)
    
    def list_templates(self) -> List[Dict[str, str]]:
        """List available templates."""
        return [
            {
                "type": t.project_type.value,
                "name": t.name,
                "description": t.description,
            }
            for t in self._templates.values()
        ]
    
    def render_template(
        self,
        template: ProjectTemplate,
        variables: Dict[str, str],
    ) -> List[FileTemplate]:
        """Render template with variables."""
        rendered = []
        
        for file in template.files:
            content = file.content
            for key, value in variables.items():
                content = content.replace(f"{{{{{key}}}}}", value)
            
            rendered.append(FileTemplate(
                path=file.path,
                content=content,
                is_binary=file.is_binary,
            ))
        
        return rendered
    
    def generate_project_files(
        self,
        project_type: ProjectType,
        project_name: str,
        additional_variables: Dict[str, str] = None,
    ) -> List[FileTemplate]:
        """Generate project files from template."""
        template = self.get_template(project_type)
        if not template:
            raise ValueError(f"Unknown project type: {project_type}")
        
        variables = {
            "project_name": project_name,
            **(additional_variables or {}),
        }
        
        return self.render_template(template, variables)


_template_engine: Optional[TemplateEngine] = None


def get_template_engine() -> TemplateEngine:
    """Get singleton template engine instance."""
    global _template_engine
    if _template_engine is None:
        _template_engine = TemplateEngine()
    return _template_engine
