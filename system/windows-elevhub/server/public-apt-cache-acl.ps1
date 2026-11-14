# The public repository is writable by the server, independently of private
# configuration/security. Never grant LocalService write access to DataRoot.
function Set-PublicAptCacheAcl([string]$DataRoot) {
    $root = [IO.Path]::GetFullPath($DataRoot)
    $cursor = Get-Item -LiteralPath $root -Force -ErrorAction Stop
    if (-not ($cursor -is [IO.DirectoryInfo])) { throw 'Data root must be a directory' }
    while ($null -ne $cursor) {
        if (($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Linked public cache path rejected' }
        $cursor = $cursor.Parent
    }
    $cache = Join-Path $root 'public-apt-cache'
    if (Test-Path -LiteralPath $cache) {
        $item = Get-Item -LiteralPath $cache -Force
        if (-not ($item -is [IO.DirectoryInfo]) -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Public cache must be a plain directory' }
    } else {
        New-Item -ItemType Directory -Path $cache -ErrorAction Stop | Out-Null
    }
    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $acl.SetAccessRuleProtection($true, $false)
    $inherit = [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
    foreach ($entry in @(@('S-1-5-18','FullControl'), @('S-1-5-32-544','FullControl'), @('S-1-5-19','Modify'))) {
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            [Security.Principal.SecurityIdentifier]::new($entry[0]),
            [Security.AccessControl.FileSystemRights]$entry[1], $inherit,
            [Security.AccessControl.PropagationFlags]::None,
            [Security.AccessControl.AccessControlType]::Allow)
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $cache -AclObject $acl -ErrorAction Stop
}
