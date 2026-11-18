variable "region" {
  type    = string
  default = "us-east-1"
}

variable "project" {
  description = "Name prefix for every resource."
  type        = string
  default     = "incident-pipeline"
}

variable "image_tag" {
  description = "Tag of both images in ECR (scripts/aws.sh push uses the git SHA)."
  type        = string
  default     = "latest"
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "db_deletion_protection" {
  description = "Set false (and apply) before `terraform destroy`."
  type        = bool
  default     = true
}

variable "pipeline_schedule" {
  description = "EventBridge Scheduler expression for the daily refresh cycle."
  type        = string
  default     = "cron(0 9 * * ? *)"
}

variable "pipeline_schedule_timezone" {
  type    = string
  default = "America/New_York"
}

variable "pipeline_cpu" {
  type    = number
  default = 1024
}

variable "pipeline_memory" {
  description = "MiB. Embeddings (sentence-transformers) need ~2-3 GB."
  type        = number
  default     = 4096
}

variable "web_desired_count" {
  type    = number
  default = 1
}
