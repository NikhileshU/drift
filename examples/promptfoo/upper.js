// Offline custom provider: uppercases the prompt. No API key, deterministic.
class UpperProvider {
  id() { return "upper"; }
  async callApi(prompt) { return { output: prompt.toUpperCase() }; }
}
module.exports = UpperProvider;
