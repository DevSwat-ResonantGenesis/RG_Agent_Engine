"""
Delivery Manager - Final Packaging
===================================

Handles final packaging and delivery of completed projects.

Features:
- Generate project archives (zip)
- Create documentation
- Generate deployment configs
- Export analysis reports
"""

import logging
import shutil
import json
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import zipfile
import os

logger = logging.getLogger(__name__)


@dataclass
class DeliveryPackage:
    """A delivery package for a completed project."""
    project_id: str
    project_name: str
    archive_path: Optional[str] = None
    files_count: int = 0
    total_size_bytes: int = 0
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    includes_docs: bool = True
    includes_analysis: bool = True
    includes_docker: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "project_name": self.project_name,
            "archive_path": self.archive_path,
            "files_count": self.files_count,
            "total_size_bytes": self.total_size_bytes,
            "total_size_mb": round(self.total_size_bytes / (1024 * 1024), 2),
            "created_at": self.created_at,
            "includes_docs": self.includes_docs,
            "includes_analysis": self.includes_analysis,
            "includes_docker": self.includes_docker,
        }


class DeliveryManager:
    """
    Manages final packaging and delivery of projects.
    """
    
    DELIVERY_PATH = os.getenv("DELIVERY_PATH", "/opt/resonant/deliveries")
    
    def __init__(self, delivery_path: str = None):
        self.delivery_path = Path(delivery_path or self.DELIVERY_PATH)
        self.delivery_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"DeliveryManager initialized with path: {self.delivery_path}")
    
    async def create_delivery_package(
        self,
        project_path: str,
        project_id: str,
        project_name: str,
        validation_result: Dict[str, Any] = None,
        build_stats: Dict[str, Any] = None,
    ) -> DeliveryPackage:
        """
        Create a delivery package for a completed project.
        
        Args:
            project_path: Path to project directory
            project_id: Project ID
            project_name: Project name
            validation_result: Code Visualizer validation result
            build_stats: Build statistics
            
        Returns:
            DeliveryPackage with archive details
        """
        project_dir = Path(project_path)
        
        if not project_dir.exists():
            raise ValueError(f"Project path does not exist: {project_path}")
        
        await self._generate_documentation(project_dir, project_name)
        
        if validation_result:
            await self._save_analysis_report(project_dir, validation_result)
        
        if build_stats:
            await self._save_build_stats(project_dir, build_stats)
        
        files_count = 0
        total_size = 0
        for item in project_dir.rglob("*"):
            if item.is_file():
                files_count += 1
                total_size += item.stat().st_size
        
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        archive_name = f"{project_name}_{timestamp}.zip"
        archive_path = self.delivery_path / archive_name
        
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for item in project_dir.rglob("*"):
                if item.is_file():
                    arcname = item.relative_to(project_dir)
                    zipf.write(item, arcname)
        
        package = DeliveryPackage(
            project_id=project_id,
            project_name=project_name,
            archive_path=str(archive_path),
            files_count=files_count,
            total_size_bytes=total_size,
        )
        
        logger.info(f"Created delivery package: {archive_path} ({files_count} files, {total_size} bytes)")
        
        return package
    
    async def _generate_documentation(self, project_dir: Path, project_name: str):
        """Generate project documentation."""
        docs_dir = project_dir / "docs"
        docs_dir.mkdir(exist_ok=True)
        
        setup_guide = f"""# {project_name} - Setup Guide

## Prerequisites

- Node.js 18+ (for frontend)
- Python 3.11+ (for backend)
- PostgreSQL 15+
- Redis 7+
- Docker (optional)

## Quick Start

### Option 1: Docker (Recommended)

```bash
docker-compose up -d
```

Access the application at:
- Frontend: http://localhost:3000
- Backend API: http://localhost:8000
- API Docs: http://localhost:8000/docs

### Option 2: Manual Setup

#### Frontend

```bash
cd frontend
npm install
npm run dev
```

#### Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate  # On Windows: venv\\Scripts\\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Environment Variables

Create a `.env` file in the backend directory:

```env
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/{project_name.lower()}
REDIS_URL=redis://localhost:6379
SECRET_KEY=your-secret-key-change-in-production
```

## Project Structure

```
{project_name}/
├── frontend/           # React + TypeScript frontend
│   ├── src/
│   │   ├── components/ # Reusable components
│   │   ├── pages/      # Page components
│   │   ├── api/        # API client
│   │   └── hooks/      # Custom hooks
│   └── package.json
├── backend/            # FastAPI backend
│   ├── app/
│   │   ├── routers/    # API endpoints
│   │   ├── models/     # Database models
│   │   ├── schemas/    # Pydantic schemas
│   │   └── core/       # Core configuration
│   └── requirements.txt
└── docker-compose.yml  # Docker configuration
```

## API Documentation

Once the backend is running, visit:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## Support

This project was generated by Resonant Project Builder.
For issues, please contact support.
"""
        
        with open(docs_dir / "SETUP.md", "w") as f:
            f.write(setup_guide)
        
        api_docs = f"""# {project_name} - API Reference

## Base URL

```
http://localhost:8000/api/v1
```

## Authentication

All endpoints require authentication via Bearer token:

```
Authorization: Bearer <token>
```

## Endpoints

### Dashboard

#### GET /dashboard
Get dashboard statistics.

**Response:**
```json
{{
  "total_revenue": 45231,
  "total_customers": 2338,
  "total_orders": 1234,
  "growth_rate": 23
}}
```

### Customers

#### GET /customers
List all customers.

#### GET /customers/{{id}}
Get customer by ID.

#### POST /customers
Create new customer.

**Request Body:**
```json
{{
  "name": "string",
  "email": "string",
  "phone": "string"
}}
```

### Orders

#### GET /orders
List all orders.

#### POST /orders
Create new order.

**Request Body:**
```json
{{
  "customer_id": 1,
  "items": [
    {{"name": "Item 1", "quantity": 2, "price": 10.00}}
  ]
}}
```

### Reservations

#### GET /reservations
List all reservations.

#### POST /reservations
Create new reservation.

**Request Body:**
```json
{{
  "customer_name": "string",
  "customer_phone": "string",
  "party_size": 4,
  "date": "2024-01-15T19:00:00Z"
}}
```
"""
        
        with open(docs_dir / "API.md", "w") as f:
            f.write(api_docs)
    
    async def _save_analysis_report(
        self,
        project_dir: Path,
        validation_result: Dict[str, Any],
    ):
        """Save Code Visualizer analysis report."""
        resonant_dir = project_dir / ".resonant"
        resonant_dir.mkdir(exist_ok=True)
        
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "analysis": validation_result,
            "summary": {
                "status": validation_result.get("status", "unknown"),
                "total_files": validation_result.get("total_files", 0),
                "total_nodes": validation_result.get("total_nodes", 0),
                "broken_connections": len(validation_result.get("broken_connections", [])),
                "governance_violations": len(validation_result.get("governance_violations", [])),
                "reachability_score": validation_result.get("reachability_score", 0),
            },
        }
        
        with open(resonant_dir / "analysis_report.json", "w") as f:
            json.dump(report, f, indent=2)
    
    async def _save_build_stats(
        self,
        project_dir: Path,
        build_stats: Dict[str, Any],
    ):
        """Save build statistics."""
        resonant_dir = project_dir / ".resonant"
        resonant_dir.mkdir(exist_ok=True)
        
        with open(resonant_dir / "build_stats.json", "w") as f:
            json.dump(build_stats, f, indent=2)
    
    async def get_delivery_package(self, archive_path: str) -> Optional[bytes]:
        """Get delivery package contents."""
        path = Path(archive_path)
        if path.exists():
            with open(path, "rb") as f:
                return f.read()
        return None
    
    async def list_deliveries(self, project_id: str = None) -> List[Dict[str, Any]]:
        """List all delivery packages."""
        deliveries = []
        
        for item in self.delivery_path.glob("*.zip"):
            stat = item.stat()
            delivery = {
                "name": item.name,
                "path": str(item),
                "size_bytes": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat(),
            }
            
            if project_id and project_id not in item.name:
                continue
            
            deliveries.append(delivery)
        
        return sorted(deliveries, key=lambda x: x["created_at"], reverse=True)
    
    async def cleanup_old_deliveries(self, days_old: int = 30) -> int:
        """Clean up old delivery packages."""
        cutoff = datetime.now(timezone.utc).timestamp() - (days_old * 24 * 60 * 60)
        deleted = 0
        
        for item in self.delivery_path.glob("*.zip"):
            if item.stat().st_ctime < cutoff:
                item.unlink()
                deleted += 1
        
        logger.info(f"Cleaned up {deleted} old delivery packages")
        return deleted


_delivery_manager: Optional[DeliveryManager] = None


def get_delivery_manager() -> DeliveryManager:
    """Get singleton delivery manager instance."""
    global _delivery_manager
    if _delivery_manager is None:
        _delivery_manager = DeliveryManager()
    return _delivery_manager
