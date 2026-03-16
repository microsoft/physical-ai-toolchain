/** TFLint configuration for deploy/001-iac and child modules */

tflint {
  required_version = ">= 0.61.0"
}

config {
  /* call local modules so modules/ subdirectories are also linted */
  call_module_type = "local"
  format           = "compact"
}

/* bundled terraform ruleset — recommended preset excludes terraform_comment_syntax */
plugin "terraform" {
  enabled = true
  preset  = "recommended"
}

/* azurerm ruleset — only available plugin for this provider stack */
plugin "azurerm" {
  enabled = true
  version = "0.31.1"
  source  = "github.com/terraform-linters/tflint-ruleset-azurerm"
}

// terraform_comment_syntax: repo uses // and /* */ comments, not #
rule "terraform_comment_syntax" {
  enabled = false
}

// terraform_documented_variables: require description on all variable blocks
rule "terraform_documented_variables" {
  enabled = true
}

// terraform_documented_outputs: require description on all output blocks
rule "terraform_documented_outputs" {
  enabled = true
}

/* terraform_naming_convention: enforce snake_case across all block types */
/* note: cannot scope to bool-typed variables only — snake_case is TFLint maximum */
rule "terraform_naming_convention" {
  enabled = true
}
