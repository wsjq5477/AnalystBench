const UNQUOTED_ARGUMENT = /^[A-Za-z0-9_@%+=:,./-]+$/;

export function formatCommandArgument(argument) {
  const text = String(argument);
  return UNQUOTED_ARGUMENT.test(text) ? text : JSON.stringify(text);
}

export function formatCommand(command) {
  if (!Array.isArray(command)) return "";
  return command.map(formatCommandArgument).join(" ");
}
