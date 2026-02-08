# API Connection Tab Documentation

## Overview

The API Connection tab manages authentication and connectivity with the Deribit API. It provides a simple interface to test the connection and view API status.

## Purpose

- Test Deribit API connectivity
- Verify authentication (if credentials configured)
- Display API status and rate limits
- Quick connection check before data operations

## Features

### 1. Connection Status
- Visual indicator (Connected/Disconnected)
- Connection timestamp
- API server URL

### 2. Test Connection Button
- Sends test request to Deribit API
- Validates authentication credentials
- Displays connection latency

### 3. API Information Display
- API version
- Rate limit status
- Last successful connection
- Error messages if connection fails

## Architecture

```
API Connection Tab (GUI)
    ↓
DeribitApiService (Core Layer)
```

**GUI**: `coding/gui/tabs/api_connection_tab.py`
**Service**: `coding/service/deribit/deribit_api_service.py`

## Usage

1. Open API Connection tab
2. Click "Test Connection"
3. View connection status and latency
4. If connection fails, check error message

## Connection Requirements

### Public Endpoints (No Auth)
- Read-only market data
- Book summary, ticker data
- No API key required

### Private Endpoints (Auth Required)
- Account information
- Order placement
- Position management
- Requires API key and secret (not implemented yet)

## Status Indicators

- **🟢 Connected**: API responding normally
- **🔴 Disconnected**: Cannot reach API or authentication failed
- **🟡 Rate Limited**: Too many requests, need to slow down

## Troubleshooting

### Connection Failed
- Check internet connectivity
- Verify firewall not blocking HTTPS
- Deribit API may be down (check status.deribit.com)

### Slow Response
- Network latency issue
- API server under load
- Try again after a few seconds

## Important Notes

- API credentials are stored in `.env` file (if configured)
- Current implementation uses public endpoints only
- No authentication required for read-only operations
- Private endpoints will require API key/secret in future

## Future Enhancements

- API key/secret configuration UI
- Account balance display
- Position summary
- Order history
- Real-time connection monitoring
