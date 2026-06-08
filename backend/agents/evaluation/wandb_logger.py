"""
W&B integration for agent evaluation
"""
import wandb
import os
from typing import Dict, Any, Optional
from datetime import datetime


class WandbLogger:
    """Handle W&B logging for agent evaluation"""
    
    def __init__(self, project: str = None, entity: str = None, run_name: str = None):
        """
        Initialize W&B logger
        
        Args:
            project: W&B project name (defaults to env var)
            entity: W&B entity name (defaults to env var)
            run_name: Name for this evaluation run
        """
        self.project = project or os.getenv('WANDB_PROJECT', 'FrontShiftAI_Agents')
        self.entity = entity or os.getenv('WANDB_ENTITY', 'group9mlops-northeastern-university')
        self.run_name = run_name or f"eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.run = None
    
    def start_run(self, config: Dict[str, Any] = None, tags: list = None):
        """
        Start a new W&B run
        
        Args:
            config: Configuration dict to log
            tags: List of tags for this run
        """
        default_tags = ["evaluation", "automated"]
        if tags:
            default_tags.extend(tags)
        
        self.run = wandb.init(
            project=self.project,
            entity=self.entity,
            name=self.run_name,
            job_type="evaluation",
            config=config or {},
            tags=default_tags,
            # wandb>=0.18: boolean reinit is deprecated; "finish_previous"
            # preserves the old True behavior.
            reinit="finish_previous",
        )
        
        print(f"✓ W&B run started: {self.run.url}")
    
    def log_intent_classification(self, metrics: Dict[str, Any], predictions: list):
        """
        Log intent classification results
        
        Args:
            metrics: Metrics dict from calculate_intent_accuracy
            predictions: List of prediction results
        """
        # Log overall metrics
        wandb.log({
            "intent_classification/accuracy": metrics['accuracy'],
            "intent_classification/correct": metrics['correct'],
            "intent_classification/total": metrics['total']
        })
        
        # Log per-class metrics
        for agent_class, class_metrics in metrics.get('class_metrics', {}).items():
            wandb.log({
                f"intent_classification/{agent_class}/precision": class_metrics['precision'],
                f"intent_classification/{agent_class}/recall": class_metrics['recall'],
                f"intent_classification/{agent_class}/f1": class_metrics['f1']
            })
        
        # Log confusion matrix data as table
        table_data = []
        for pred in predictions:
            table_data.append([
                pred['id'],
                pred['input'][:50],
                pred['expected'],
                pred['predicted'],
                pred['correct']
            ])
        
        table = wandb.Table(
            columns=["test_id", "input", "expected", "predicted", "correct"],
            data=table_data
        )
        wandb.log({"intent_classification/predictions": table})
    
    def log_agent_metrics(self, agent_name: str, metrics: Dict[str, Any]):
        """
        Log metrics for a specific agent
        
        Args:
            agent_name: Name of the agent (pto, hr_ticket, website_extraction)
            metrics: Metrics dict from calculate_agent_metrics
        """
        prefix = f"{agent_name}"
        
        # Latency metrics
        wandb.log({
            f"{prefix}/latency_avg_ms": metrics['latency']['avg_ms'],
            f"{prefix}/latency_p50_ms": metrics['latency']['p50_ms'],
            f"{prefix}/latency_p95_ms": metrics['latency']['p95_ms'],
            f"{prefix}/latency_p99_ms": metrics['latency']['p99_ms'],
            f"{prefix}/latency_min_ms": metrics['latency']['min_ms'],
            f"{prefix}/latency_max_ms": metrics['latency']['max_ms']
        })
        
        # Success metrics
        wandb.log({
            f"{prefix}/success_rate": metrics['success']['success_rate'],
            f"{prefix}/failure_rate": metrics['success']['failure_rate'],
            f"{prefix}/total_tests": metrics['success']['total']
        })
        
        # Quality metrics
        wandb.log({
            f"{prefix}/quality_avg": metrics['quality']['avg_completeness'],
            f"{prefix}/quality_min": metrics['quality']['min_completeness'],
            f"{prefix}/quality_max": metrics['quality']['max_completeness']
        })
    
    def log_agent_results(self, agent_name: str, results: list):
        """
        Log detailed results for an agent as a table
        
        Args:
            agent_name: Name of the agent
            results: List of result dicts
        """
        table_data = []
        for result in results[:50]:  # Limit to 50 for readability
            table_data.append([
                result.get('id', ''),
                result.get('input', '')[:50],
                result.get('success', False),
                result.get('latency_ms', 0),
                result.get('response', '')[:100]
            ])
        
        table = wandb.Table(
            columns=["test_id", "input", "success", "latency_ms", "response"],
            data=table_data
        )
        wandb.log({f"{agent_name}/results_sample": table})
    
    def log_summary(self, summary_text: str):
        """
        Log summary report
        
        Args:
            summary_text: Formatted summary string
        """
        wandb.log({"evaluation/summary": wandb.Html(f"<pre>{summary_text}</pre>")})
    
    def log_performance_targets(self, targets: Dict[str, bool]):
        """
        Log whether performance targets were met
        
        Args:
            targets: Dict of target_name -> passed (bool)
        """
        for target_name, passed in targets.items():
            wandb.log({f"targets/{target_name}": 1 if passed else 0})
    
    def finish(self):
        """Finish the W&B run"""
        if self.run:
            wandb.finish()
            print("✓ W&B run finished")


def create_alert(title: str, text: str, level: str = "WARN"):
    """
    Create a W&B alert
    
    Args:
        title: Alert title
        text: Alert message
        level: Alert level (INFO, WARN, ERROR)
    """
    alert_level = getattr(wandb.AlertLevel, level, wandb.AlertLevel.WARN)
    wandb.alert(
        title=title,
        text=text,
        level=alert_level
    )