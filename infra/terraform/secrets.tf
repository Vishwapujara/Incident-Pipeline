# DATABASE_URL is generated here. TLS settings are passed separately
# (PGSSLMODE / DB_SSL_CA_FILE) so the same URL works for both apps.
resource "aws_secretsmanager_secret" "database_url" {
  name                    = "${var.project}/database-url"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "database_url" {
  secret_id = aws_secretsmanager_secret.database_url.id
  secret_string = format("postgresql://%s:%s@%s/%s",
    aws_db_instance.main.username, random_password.db.result,
  aws_db_instance.main.endpoint, aws_db_instance.main.db_name)
}

# The Groq key is NOT a Terraform input, so it never lands in state.
# Set it once after apply:
#   aws secretsmanager put-secret-value --secret-id <groq_secret_arn> --secret-string "$GROQ_API_KEY"
resource "aws_secretsmanager_secret" "groq_api_key" {
  name                    = "${var.project}/groq-api-key"
  recovery_window_in_days = 0
}
