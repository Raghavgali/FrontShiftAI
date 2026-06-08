"""
Production monitoring logger for FrontShiftAI
Tracks real-time metrics in production using WANDB with built-in alerting
"""
import os
import time
import logging
from typing import Dict, Any, Optional
import wandb
from datetime import datetime

# Setup logger for alerts
logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)

class ProductionMonitor:
    """Monitor production requests and system metrics with alerting"""
    
    def __init__(self):
        self.run = None
        self._initialize_wandb()
        
        # Alert thresholds
        self.thresholds = {
            'max_latency_ms': 3000,           # 3 seconds
            'max_error_rate': 0.05,           # 5%
            'min_agent_success_rate': 0.90,   # 90%
            'max_db_query_time_ms': 1000,     # 1 second
            'max_agent_execution_ms': 5000    # 5 seconds
        }
        
        # Track metrics for rate calculation
        self.request_count = 0
        self.error_count = 0
        self.agent_failures = {}
        self.agent_attempts = {}
    
    def _initialize_wandb(self):
        """Initialize WANDB for production monitoring"""
        try:
            project_name = os.getenv("WANDB_PRODUCTION_PROJECT", "FrontShiftAI_Production")
            env_type = os.getenv("ENVIRONMENT", "development")

            # Default to the authed user's own entity (let wandb pick) instead
            # of hardcoding the old team's entity. The original group9mlops
            # entity is no longer writable from this account.
            entity = os.getenv("WANDB_ENTITY") or None

            self.run = wandb.init(
                project=project_name,
                entity=entity,
                name=f"{env_type}-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
                job_type="production-monitoring",
                tags=[env_type, "monitoring"],
                # Phase-7 cleanup: reinit=True is deprecated in wandb>=0.18.
                # "finish_previous" matches the old boolean-True behavior
                # (close any active run before starting a new one).
                reinit="finish_previous",
            )
            print(f"✅ Production monitoring initialized with WANDB (Project: {project_name})")
        except Exception as e:
            print(f"⚠️ WANDB initialization failed: {e}")
            self.run = None
    
    def _check_alert(self, metric_name: str, value: float, threshold: float, comparison: str = "greater"):
        """Check if a metric exceeds threshold and log alert"""
        alert_triggered = False
        
        if comparison == "greater" and value > threshold:
            alert_triggered = True
        elif comparison == "less" and value < threshold:
            alert_triggered = True
        
        if alert_triggered:
            logger.warning(
                f"🚨 ALERT: {metric_name} = {value:.2f} (threshold: {threshold:.2f})"
            )
            
            # Log to WANDB as well
            if self.run:
                wandb.log({
                    f"alert/{metric_name}": 1,
                    f"alert/{metric_name}_value": value,
                    "timestamp": time.time()
                })
    
    def log_request(
        self,
        endpoint: str,
        method: str,
        status_code: int,
        latency_ms: float,
        company_id: Optional[str] = None,
        user_id: Optional[str] = None,
        error: Optional[str] = None
    ):
        """Log API request metrics with alerting"""
        if not self.run:
            return
        
        # Track for error rate calculation
        self.request_count += 1
        if status_code >= 400:
            self.error_count += 1
            # Log error to Cloud Logging (standard logger)
            log_level = logging.ERROR if status_code >= 500 else logging.WARNING
            logger.log(log_level, f"API Error [{status_code}] {method} {endpoint}: {error or 'Unknown Error'}")

        
        # Check latency alert
        self._check_alert("request_latency_ms", latency_ms, self.thresholds['max_latency_ms'])
        
        # Check error rate (every 100 requests)
        if self.request_count % 100 == 0:
            error_rate = self.error_count / self.request_count
            self._check_alert("error_rate", error_rate, self.thresholds['max_error_rate'])
        
        metrics = {
            "request/endpoint": endpoint,
            "request/method": method,
            "request/status_code": status_code,
            "request/latency_ms": latency_ms,
            "request/success": 1 if status_code < 400 else 0,
            "request/error": 1 if status_code >= 400 else 0,
            "timestamp": time.time()
        }
        
        if company_id:
            metrics["request/company_id"] = company_id
        if user_id:
            metrics["request/user_id"] = user_id
        if error:
            metrics["request/error_message"] = error
        
        wandb.log(metrics)
    
    def log_agent_execution(
        self,
        agent_name: str,
        execution_time_ms: float,
        success: bool,
        token_count: Optional[int] = None,
        company_id: Optional[str] = None
    ):
        """Log agent execution metrics with alerting"""
        if not self.run:
            return
        
        # Track agent success rate
        if agent_name not in self.agent_attempts:
            self.agent_attempts[agent_name] = 0
            self.agent_failures[agent_name] = 0
        
        self.agent_attempts[agent_name] += 1
        if not success:
            self.agent_failures[agent_name] += 1
            # Log agent failure to standard logger
            logger.warning(f"Agent Failure [{agent_name}]: Execution failed after {execution_time_ms}ms")
        
        # Check execution time
        self._check_alert(f"agent_{agent_name}_execution_ms", execution_time_ms, self.thresholds['max_agent_execution_ms'])
        
        # Check success rate (every 10 attempts)
        if self.agent_attempts[agent_name] % 10 == 0:
            success_rate = 1 - (self.agent_failures[agent_name] / self.agent_attempts[agent_name])
            self._check_alert(f"agent_{agent_name}_success_rate", success_rate, self.thresholds['min_agent_success_rate'], comparison="less")
        
        metrics = {
            f"agent/{agent_name}/execution_time_ms": execution_time_ms,
            f"agent/{agent_name}/success": 1 if success else 0,
            f"agent/{agent_name}/failure": 0 if success else 1,
            "timestamp": time.time()
        }
        
        if token_count:
            metrics[f"agent/{agent_name}/tokens"] = token_count
        if company_id:
            metrics[f"agent/{agent_name}/company_id"] = company_id
        
        wandb.log(metrics)
    
    def log_rag_metrics(
        self,
        query: str,
        docs_retrieved: int,
        retrieval_time_ms: float,
        relevance_score: Optional[float] = None,
        company_id: Optional[str] = None
    ):
        """Log RAG/ChromaDB retrieval metrics"""
        if not self.run:
            return
        
        metrics = {
            "rag/query_length": len(query),
            "rag/docs_retrieved": docs_retrieved,
            "rag/retrieval_time_ms": retrieval_time_ms,
            "timestamp": time.time()
        }
        
        if relevance_score:
            metrics["rag/relevance_score"] = relevance_score
        if company_id:
            metrics["rag/company_id"] = company_id
        
        wandb.log(metrics)
    
    def log_database_query(
        self,
        query_type: str,
        execution_time_ms: float,
        rows_affected: Optional[int] = None
    ):
        """Log database query performance with alerting"""
        if not self.run:
            return
        
        # Check database query time
        self._check_alert(f"database_{query_type}_time_ms", execution_time_ms, self.thresholds['max_db_query_time_ms'])
        
        metrics = {
            f"database/{query_type}/execution_time_ms": execution_time_ms,
            "timestamp": time.time()
        }
        
        if rows_affected is not None:
            metrics[f"database/{query_type}/rows_affected"] = rows_affected
        
        wandb.log(metrics)
    
    def log_api_call(
        self,
        api_name: str,
        latency_ms: float,
        success: bool,
        token_count: Optional[int] = None
    ):
        """Log external API calls (Mercury, Groq, etc.)"""
        if not self.run:
            return
        
        metrics = {
            f"api/{api_name}/latency_ms": latency_ms,
            f"api/{api_name}/success": 1 if success else 0,
            f"api/{api_name}/failure": 0 if success else 1,
            "timestamp": time.time()
        }
        
        if token_count:
            metrics[f"api/{api_name}/tokens"] = token_count
        
        wandb.log(metrics)
    
    def close(self):
        """Close WANDB run"""
        if self.run:
            self.run.finish()


# Global instance
# Global instance
production_monitor = ProductionMonitor()

# ## **What This Does:**
#
# ### **Automatic Alerts When:**
# 1. ⚠️ **Request latency > 3 seconds** → Logs warning immediately
# 2. ⚠️ **Error rate > 5%** → Checked every 100 requests
# 3. ⚠️ **Agent success rate < 90%** → Checked every 10 attempts per agent
# 4. ⚠️ **Database query > 1 second** → Logs warning immediately
# 5. ⚠️ **Agent execution > 5 seconds** → Logs warning immediately
#
# ### **Alert Output:**
# ```
# 🚨 ALERT: request_latency_ms = 3500.00 (threshold: 3000.00)
# 🚨 ALERT: agent_pto_success_rate = 0.85 (threshold: 0.90)
# 🚨 ALERT: database_pto_balance_check_time_ms = 1200.00 (threshold: 1000.00)
# ```