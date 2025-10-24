# White Agent GUI Usage Guide

## 🚀 Quick Start

### 1. Start Services

```bash
# 1. Start White Agent
python set_white_agent.py

# 2. Start Green Agent  
python set_green_agent.py

# 3. Start Web Server
python web_server.py
```

### 2. Using the GUI

1. **Open Browser** and navigate to `http://localhost:8080`

2. **Connect to White Agent**:
   - Enter White Agent URL in the input field (default: `http://localhost:8001`)
   - Click "🔗 Connect to White Agent" button
   - System will automatically fetch and display the White Agent's persona description

3. **Run Evaluation**:
   - After successful connection, click "▶️ Run Test" button
   - System will use Green Agent to perform complete evaluation of White Agent
   - Real-time display of evaluation progress and results

## 🔧 New Features

### Backend API Endpoints

- **`POST /connect_white_agent`**: Connect to White Agent and get persona description
- **`POST /evaluate_with_white_agent`**: Perform complete evaluation using White Agent
- **`POST /evaluate_with_white_agent_stream`**: Stream evaluation with real-time progress display

### Frontend Interface Updates

- **White Agent URL Input**: Enter White Agent address
- **Connection Button**: Test connection to White Agent
- **Persona Display Area**: Show persona description fetched from White Agent
- **Real-time Evaluation Progress**: Display various stages of evaluation process

## 🔄 Workflow

```
1. User enters White Agent URL
2. GUI connects to White Agent, fetches persona description
3. User clicks "Run Test"
4. GUI calls Green Agent for evaluation
5. Green Agent interacts with White Agent, generates questions and scores
6. Real-time display of evaluation progress and final results
```

## 📊 Evaluation Results

Evaluation results include the following dimensions:
- **Expected Action**: Expected behavior
- **Toxicity**: Toxicity detection
- **Linguistic Habits**: Language habits
- **Persona Consistency**: Persona consistency
- **Action Justification**: Action justification

## 🛠️ Technical Implementation

### Backend Modifications
- Added White Agent connection functionality
- Integrated kick_off.py evaluation logic
- Support for streaming evaluation progress display

### Frontend Modifications
- Replaced persona input field with White Agent URL input
- Added connection status display
- Integrated real-time evaluation progress display

## 🎉 Advantages

1. **Visual Operation**: No command line operations required
2. **Real-time Feedback**: Display evaluation progress and status
3. **Error Handling**: Comprehensive error prompts and exception handling
4. **User-friendly**: Intuitive interface and operation flow

## 🔍 Troubleshooting

### Common Issues

1. **Connection Failed**: Ensure White Agent is running
2. **Evaluation Failed**: Ensure Green Agent is running
3. **Port Conflict**: Check if ports are occupied

### Debug Steps

1. Check if all services are running normally
2. Check browser console for error messages
3. Check backend log output
