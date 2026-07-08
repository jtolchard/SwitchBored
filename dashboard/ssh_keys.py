import os
import glob
import shlex
import subprocess

class SSHKeyAssistant:
    """Utility helpers for discovering, generating, and deploying SSH keys."""

    @staticmethod
    def get_existing_keys():
        """Return a list of existing public SSH key files in the user's .ssh directory."""
        ssh_dir = os.path.expanduser("~/.ssh/*.pub")
        return glob.glob(ssh_dir)

    @staticmethod
    def generate_key():
        """Create a default ed25519 SSH key if one does not already exist."""
        ssh_dir = os.path.expanduser("~/.ssh")
        os.makedirs(ssh_dir, mode=0o700, exist_ok=True)
        key_path = os.path.join(ssh_dir, "id_ed25519")
        
        if os.path.exists(key_path):
            return True, "Key already exists."
            
        cmd = ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", key_path]
        res = subprocess.run(cmd, capture_output=True, text=True)
        return res.returncode == 0, (res.stdout or "") + (res.stderr or "")

    @staticmethod
    def deploy_all_serial(core, machines, terminal_type, key_path):
        """Open a terminal window and run ssh-copy-id serially for each target machine."""
        if core:
            core.log("SSH DEPLOY", f"Starting serial deployment using key: {os.path.basename(key_path)}")

        if not machines: return
        title_cmd = "echo -n -e '\\033]0;SwitchBored Key Deployment\\007'"
        commands = [title_cmd, "echo '--- STARTING SWITCHBORED KEY DEPLOYMENT ---'"]
        
        for m in machines:
            user = m.get('user', 'root')
            addr = m.get('address')
            port = m.get('port', 22)
            
            if not addr: continue 
                
            target_info = f"Target: {m.get('name', 'Unknown')} ({addr})"
            
            ssh_cmd = (
                f"ssh-copy-id -i {shlex.quote(key_path)} "
                f"-p {shlex.quote(str(port))} {shlex.quote(f'{user}@{addr}')}"
            )
            
            commands.append(f"echo {shlex.quote(target_info)}")
            commands.append(ssh_cmd)
        
        commands.append("echo '--- DEPLOYMENT COMPLETE ---'")
        bash_logic = " ; ".join(commands)
        full_shell_command = f"bash -c {shlex.quote(bash_logic)} ; exec $SHELL"
        
        if terminal_type == "iTerm":
            ascript = '''
            on run argv
                tell application "iTerm"
                    activate
                    create window with default profile command (item 1 of argv)
                end tell
            end run
            '''
        else:
            ascript = '''
            on run argv
                tell application "Terminal"
                    activate
                    do script (item 1 of argv)
                end tell
            end run
            '''
        subprocess.run(["osascript", "-e", ascript, full_shell_command])