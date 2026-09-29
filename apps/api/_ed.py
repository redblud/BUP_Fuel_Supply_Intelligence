def edit(p, pairs):
    s=open(p,newline='').read()
    nl='\r\n' if '\r\n' in s else '\n'
    for a,b in pairs:
        a=a.replace('\n',nl); b=b.replace('\n',nl)
        assert a in s, (p,a)
        s=s.replace(a,b,1)
    open(p,'w',newline='').write(s)
