param(
    [string]$RootPath = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'

function Ensure-Directory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

function Ensure-File {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [string]$Content = ''
    )

    $parent = Split-Path -Parent $Path
    if ($parent) {
        Ensure-Directory -Path $parent
    }

    if (-not (Test-Path -LiteralPath $Path)) {
        Set-Content -LiteralPath $Path -Value $Content -Encoding utf8
    }
}

function Ensure-InitFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    Ensure-File -Path $Path -Content ''
}

Write-Host "Bootstrap is running in: $RootPath"

$rootDirectories = @(
    'docs',
    'docs\architecture',
    'docs\api',
    'docs\thesis',
    'plugins',
    'plugins\template',
    'plugins\custom',
    'data',
    'data\raw',
    'data\processed',
    'data\cases',
    'data\evidence',
    'data\exports',
    'reports',
    'reports\pdf',
    'reports\csv',
    'reports\figures',
    'logs',
    'docker',
    'backend',
    'backend\app',
    'backend\app\api',
    'backend\app\api\v1',
    'backend\app\api\v1\routes',
    'backend\app\core',
    'backend\app\models',
    'backend\app\schemas',
    'backend\app\services',
    'backend\app\repositories',
    'backend\app\analytics',
    'backend\app\analytics\ingestion',
    'backend\app\analytics\normalization',
    'backend\app\analytics\graph_building',
    'backend\app\analytics\risk_scoring',
    'backend\app\analytics\clustering',
    'backend\app\analytics\anomaly_detection',
    'backend\app\analytics\peel_chains',
    'backend\app\analytics\chain_hopping',
    'backend\app\analytics\blacklist_check',
    'backend\app\analytics\path_finding',
    'backend\app\analytics\plugins',
    'backend\app\evidence',
    'backend\app\evidence\hashing',
    'backend\app\evidence\chain_of_custody',
    'backend\app\evidence\audit_log',
    'backend\app\reports',
    'backend\app\exports',
    'backend\app\utils',
    'backend\tests',
    'backend\scripts',
    'frontend',
    'frontend\src',
    'frontend\src\app',
    'frontend\src\app\core',
    'frontend\src\app\core\interceptors',
    'frontend\src\app\core\guards',
    'frontend\src\app\core\services',
    'frontend\src\app\shared',
    'frontend\src\app\shared\components',
    'frontend\src\app\shared\pipes',
    'frontend\src\app\features',
    'frontend\src\app\features\dashboard',
    'frontend\src\app\features\search',
    'frontend\src\app\features\timeline',
    'frontend\src\app\features\graph-visualization',
    'frontend\src\app\features\case-management',
    'frontend\src\app\features\evidence-locker',
    'frontend\src\app\features\report-export',
    'frontend\src\app\features\settings',
    'frontend\src\app\layout',
    'frontend\src\app\layout\shell',
    'frontend\src\app\layout\sidebar',
    'frontend\src\app\layout\topbar',
    'frontend\src\app\models',
    'frontend\src\app\services',
    'frontend\src\assets',
    'frontend\src\environments'
)

$rootDirectories | ForEach-Object {
    Ensure-Directory -Path (Join-Path $RootPath $_)
}

$files = @{
    '.gitignore' = @'
# Node
node_modules/
dist/
.angular/

# Python
.venv/
__pycache__/
*.pyc

# IDE
.vscode/

# Logs and exports
logs/
reports/
data/exports/
'@;
    'backend\requirements.txt' = @'
fastapi
uvicorn[standard]
pandas
networkx
python-multipart
pydantic-settings
python-dotenv
scikit-learn
'@;
    'backend\.env.example' = @'
APP_NAME=Lusi v1.0
APP_ENV=development
APP_HOST=0.0.0.0
APP_PORT=8000
CORS_ORIGINS=http://localhost:4200
'@;
    'backend\app\main.py' = @'
from fastapi import FastAPI

from app.api.v1.router import api_router


app = FastAPI(title='Lusi v1.0 API', version='1.0.0')
app.include_router(api_router, prefix='/api/v1')


@app.get('/health')
def health_check() -> dict[str, str]:
    return {'status': 'ok'}


@app.get('/')
def root() -> dict[str, str]:
    return {'message': 'Lusi v1.0 backend is running'}
'@;
    'backend\app\api\v1\router.py' = @'
from fastapi import APIRouter


api_router = APIRouter()
'@;
    'frontend\package.json' = @'
{
  "name": "lusi-frontend",
  "version": "1.0.0",
  "private": true,
  "scripts": {
    "start": "ng serve",
    "build": "ng build",
    "test": "ng test"
  }
}
'@;
    'frontend\angular.json' = @'
{
  "$schema": "https://json.schemastore.org/angular.json",
  "version": 1,
  "defaultProject": "lusi-frontend"
}
'@;
    'frontend\tsconfig.json' = @'
{
  "compileOnSave": false,
  "compilerOptions": {
    "baseUrl": ".",
    "paths": {
      "@app/*": ["src/app/*"]
    }
  }
}
'@;
    'frontend\tsconfig.app.json' = @'
{
  "extends": "./tsconfig.json",
  "compilerOptions": {
    "outDir": "./out-tsc/app"
  },
  "files": ["src/main.ts"],
  "include": ["src/**/*.d.ts"]
}
'@;
    'frontend\tsconfig.spec.json' = @'
{
  "extends": "./tsconfig.json",
  "compilerOptions": {
    "outDir": "./out-tsc/spec"
  },
  "files": ["src/test.ts"],
  "include": ["src/**/*.spec.ts", "src/**/*.d.ts"]
}
'@;
    'frontend\src\main.ts' = @'
import { bootstrapApplication } from '@angular/platform-browser';

import { appConfig } from './app/app.config';
import { AppComponent } from './app/app.component';

bootstrapApplication(AppComponent, appConfig).catch((error) => console.error(error));
'@;
    'frontend\src\index.html' = @'
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>Lusi v1.0</title>
    <base href="/">
    <meta name="viewport" content="width=device-width, initial-scale=1">
  </head>
  <body>
    <app-root></app-root>
  </body>
</html>
'@;
    'frontend\src\styles.scss' = @'
html, body {
  margin: 0;
  min-height: 100%;
}
'@;
    'frontend\src\test.ts' = '';
    'frontend\src\app\app.config.ts' = @'
import { ApplicationConfig } from '@angular/core';
import { provideRouter } from '@angular/router';

import { appRoutes } from './app.routes';

export const appConfig: ApplicationConfig = {
  providers: [provideRouter(appRoutes)],
};
'@;
    'frontend\src\app\app.routes.ts' = @'
import { Routes } from '@angular/router';

export const appRoutes: Routes = [];
'@;
    'frontend\src\app\app.component.ts' = @'
import { Component } from '@angular/core';
import { RouterOutlet } from '@angular/router';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet],
  templateUrl: './app.component.html',
  styleUrl: './app.component.scss',
})
export class AppComponent {}
'@;
    'frontend\src\app\app.component.html' = @'
<main class="app-shell">
  <section class="hero">
    <p class="eyebrow">Lusi v1.0</p>
    <h1>Blockchain forensics workspace</h1>
    <p>Open-source modular tool for analysis, evidence handling, and reporting.</p>
    <router-outlet></router-outlet>
  </section>
</main>
'@;
    'frontend\src\app\app.component.scss' = @'
.app-shell {
  min-height: 100vh;
  display: grid;
  place-items: center;
  font-family: Arial, sans-serif;
  padding: 2rem;
}

.hero {
  max-width: 48rem;
  width: 100%;
  text-align: center;
}

.eyebrow {
  text-transform: uppercase;
  letter-spacing: 0.2em;
  font-size: 0.75rem;
}
'@;
    'frontend\src\app\core\core.module.ts' = @'
export {};
'@;
    'frontend\src\app\shared\shared.module.ts' = @'
export {};
'@;
    'frontend\src\environments\environment.ts' = @'
export const environment = {
  production: false,
  apiUrl: 'http://localhost:8000',
};
'@;
    'frontend\src\environments\environment.development.ts' = @'
export const environment = {
  production: false,
  apiUrl: 'http://localhost:8000',
};
'@;
}

$files.Keys | ForEach-Object {
    Ensure-File -Path (Join-Path $RootPath $_) -Content $files[$_]
}

$initFiles = @(
    'backend\app\__init__.py',
    'backend\app\api\__init__.py',
    'backend\app\api\v1\__init__.py',
    'backend\app\api\v1\routes\__init__.py',
    'backend\app\core\__init__.py',
    'backend\app\models\__init__.py',
    'backend\app\schemas\__init__.py',
    'backend\app\services\__init__.py',
    'backend\app\repositories\__init__.py',
    'backend\app\analytics\__init__.py',
    'backend\app\analytics\ingestion\__init__.py',
    'backend\app\analytics\normalization\__init__.py',
    'backend\app\analytics\graph_building\__init__.py',
    'backend\app\analytics\risk_scoring\__init__.py',
    'backend\app\analytics\clustering\__init__.py',
    'backend\app\analytics\anomaly_detection\__init__.py',
    'backend\app\analytics\peel_chains\__init__.py',
    'backend\app\analytics\chain_hopping\__init__.py',
    'backend\app\analytics\blacklist_check\__init__.py',
    'backend\app\analytics\path_finding\__init__.py',
    'backend\app\analytics\plugins\__init__.py',
    'backend\app\evidence\__init__.py',
    'backend\app\evidence\hashing\__init__.py',
    'backend\app\evidence\chain_of_custody\__init__.py',
    'backend\app\evidence\audit_log\__init__.py',
    'backend\app\reports\__init__.py',
    'backend\app\exports\__init__.py',
    'backend\app\utils\__init__.py'
)

$initFiles | ForEach-Object {
    Ensure-InitFile -Path (Join-Path $RootPath $_)
}

Write-Host 'Bootstrap completed successfully.'