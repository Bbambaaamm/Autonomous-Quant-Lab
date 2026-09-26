import * as THREE from 'three';

const STATE_META={
  idle:{label:'Čeká',copy:'Klidový režim · žádná aktivní práce',tone:'amber'},
  receiving:{label:'Přijímá zadání',copy:'Nové zadání bylo přijato',tone:'amber'},
  working:{label:'Zpracovává úlohu',copy:'Agent je podle živého stavu označen jako working',tone:'amber'},
  tool:{label:'Používá nástroj',copy:'Demo nástrojové operace · živý zdroj tuto událost neposkytuje',tone:'green'},
  delegating:{label:'Deleguje',copy:'Demo předání úkolu vybranému agentovi',tone:'green'},
  waiting_result:{label:'Čeká na výsledek',copy:'Demo čekání na odpověď vybraného agenta',tone:'amber'},
  waiting_user:{label:'Čeká na zásah',copy:'Agent je blokovaný a potřebuje kontrolu',tone:'red'},
  speaking:{label:'Mluví',copy:'Stylizovaná animace odpovědi · audio není připojeno',tone:'green'},
  complete:{label:'Úloha dokončena',copy:'Agent ohlásil stav done',tone:'green'},
  error:{label:'Problém systému',copy:'Jeden nebo více datových zdrojů selhalo',tone:'red'},
  offline:{label:'Odpojeno',copy:'Živý stav koordinátora není dostupný',tone:'muted'}
};
const AGENT_ROLES={hermes:'Orchestrace, paměť, nástroje a řízení práce',codex:'Implementace, testování a technická revize'};
const fmt=new Intl.NumberFormat('cs-CZ');
const money=micro=>micro==null?'Neznámé':micro===0?'0 USD':`${(micro/1e6).toFixed(4)} USD`;
const ago=(stamp,now=Date.now()/1000)=>stamp==null?'Nedostupné':`${Math.max(0,Math.round(now-stamp))} s`;
const safeText=value=>value==null?'Nedostupné':String(value);

function createFaceController(canvas,fallback){
  let renderer;
  try{renderer=new THREE.WebGLRenderer({canvas,antialias:true,powerPreference:'high-performance',alpha:true});}
  catch(error){fallback.hidden=false;return null;}
  const low=(navigator.deviceMemory&&navigator.deviceMemory<=4)||innerWidth<760;
  const rand=(a,b)=>a+Math.random()*(b-a),matrix=new THREE.Matrix4(),quat=new THREE.Quaternion(),scale=new THREE.Vector3();
  renderer.setPixelRatio(Math.min(devicePixelRatio,low?1.12:1.6));renderer.setSize(innerWidth,innerHeight);
  renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.27;renderer.setClearColor(0x000000,0);
  const scene=new THREE.Scene();scene.background=null;scene.fog=new THREE.FogExp2(0x171410,.027);
  const camera=new THREE.PerspectiveCamera(34,innerWidth/innerHeight,.1,90);camera.position.set(0,.18,12.35);

  function skyTexture(){
    const c=document.createElement('canvas');c.width=1024;c.height=512;const x=c.getContext('2d');
    const g=x.createLinearGradient(0,0,0,512);g.addColorStop(0,'#504a41');g.addColorStop(.28,'#292720');g.addColorStop(.68,'#141310');g.addColorStop(1,'#060606');x.fillStyle=g;x.fillRect(0,0,1024,512);
    for(let i=0;i<55;i++){const px=rand(0,1024),py=rand(0,330),r=rand(35,180),cloud=x.createRadialGradient(px,py,0,px,py,r);cloud.addColorStop(0,`rgba(${rand(75,125)|0},${rand(65,95)|0},${rand(50,70)|0},${rand(.03,.12)})`);cloud.addColorStop(1,'rgba(0,0,0,0)');x.fillStyle=cloud;x.fillRect(px-r,py-r,r*2,r*2);}
    const glow=x.createRadialGradient(520,230,5,520,230,320);glow.addColorStop(0,'rgba(219,147,63,.2)');glow.addColorStop(1,'rgba(25,19,13,0)');x.fillStyle=glow;x.fillRect(170,-90,700,650);
    const texture=new THREE.CanvasTexture(c);texture.colorSpace=THREE.SRGBColorSpace;return texture;
  }
  const sky=new THREE.Mesh(new THREE.PlaneGeometry(34,17),new THREE.MeshBasicMaterial({map:skyTexture(),fog:false}));sky.position.set(0,1,-17);scene.add(sky);

  const city=new THREE.Group();scene.add(city);
  const towerMat=new THREE.MeshStandardMaterial({color:0x191814,metalness:.9,roughness:.48,emissive:0x2d1d0f,emissiveIntensity:.38});
  const towerGeo=new THREE.BoxGeometry(1,1,1),towerCount=low?115:230,towers=new THREE.InstancedMesh(towerGeo,towerMat,towerCount),spires=new THREE.InstancedMesh(new THREE.ConeGeometry(.5,1,5),towerMat,towerCount);
  const cityLights=[];
  for(let i=0;i<towerCount;i++){
    const edge=Math.random()<.7?rand(4.1,17)*Math.sign(rand(-1,1)):rand(-17,17),z=rand(-15,-5.2),h=rand(1.2,8.8),w=rand(.16,.88),y=-3.2+h/2;
    matrix.compose(new THREE.Vector3(edge,y,z),new THREE.Quaternion(),new THREE.Vector3(w,h,w*rand(.7,1.5)));towers.setMatrixAt(i,matrix);
    matrix.compose(new THREE.Vector3(edge,-3.2+h+rand(.2,.8),z),new THREE.Quaternion(),new THREE.Vector3(w*.48,rand(.5,2.1),w*.48));spires.setMatrixAt(i,matrix);
    const rows=Math.max(1,Math.floor(h/.42));for(let j=0;j<rows;j++)if(Math.random()>.47)cityLights.push(edge+rand(-w*.36,w*.36),-3+h*(j/rows)+rand(-.05,.05),z+.52*w);
  }
  towers.instanceMatrix.needsUpdate=true;spires.instanceMatrix.needsUpdate=true;city.add(towers,spires);
  const cityGeo=new THREE.BufferGeometry();cityGeo.setAttribute('position',new THREE.Float32BufferAttribute(cityLights,3));city.add(new THREE.Points(cityGeo,new THREE.PointsMaterial({color:0xffae52,size:low?.025:.034,transparent:true,opacity:.8,blending:THREE.AdditiveBlending,depthWrite:false})));
  function tube(points,r=.025,color=0x6d5130,emissive=.18,segments=28){return new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(points),segments,r,6,false),new THREE.MeshStandardMaterial({color,metalness:.92,roughness:.31,emissive:color,emissiveIntensity:emissive}));}
  for(let i=0;i<(low?12:22);i++){const side=i%2?1:-1,y=rand(-1.8,4.4),z=rand(-11,-6);city.add(tube([new THREE.Vector3(side*rand(4.2,7.5),y,z),new THREE.Vector3(side*rand(7.5,11),y+rand(-.6,.6),z-.3),new THREE.Vector3(side*rand(11,16),y+rand(-.5,.5),z)],rand(.018,.055),0x332719,.06));}

  const root=new THREE.Group();root.position.y=.18;scene.add(root);const face=new THREE.Group();face.scale.setScalar(.94);face.rotation.y=.055;root.add(face);
  const shellMat=new THREE.MeshStandardMaterial({vertexColors:true,metalness:.82,roughness:.43,side:THREE.DoubleSide});
  function facialWidth(y){const n=(y+2.8)/5.8;let w=1.72+.68*Math.sin(Math.PI*Math.min(1,Math.max(0,n)));if(y<-1.25)w*=1-(Math.abs(y+1.25)*.23);return Math.max(.72,w);}
  function surface(nx,y){
    const w=facialWidth(y),x=nx*w,edge=Math.sqrt(Math.max(.02,1-nx*nx));let z=.12+1.12*edge;
    z+=.20*Math.exp(-((Math.abs(x)-1.03)**2)*4.8-((y+.02)**2)*1.8);
    z+=.38*Math.exp(-(x*x)*3.2-((y-.48)**2)*.46);
    z-=.34*Math.exp(-((Math.abs(x)-.78)**2)*17-((y-.66)**2)*14);
    z-=.15*Math.exp(-(x*x)*4-((y+1.02)**2)*14);
    return new THREE.Vector3(x,y,z);
  }
  const rows=low?34:50,cols=low?32:46,verts=[],colors=[],indices=[],bronze=new THREE.Color(0x684d31),charcoal=new THREE.Color(0x45413a),bone=new THREE.Color(0x756f66);
  for(let r=0;r<=rows;r++){const y=-2.8+r/rows*5.8;for(let c=0;c<=cols;c++){const nx=-1+c/cols*2,p=surface(nx,y);verts.push(p.x,p.y,p.z);const edge=Math.abs(nx),light=.72+.38*Math.max(0,1-edge)+rand(-.08,.08),base=(Math.random()<.07?bronze:Math.random()<.34?bone:charcoal).clone().multiplyScalar(light);colors.push(base.r,base.g,base.b);}}
  for(let r=0;r<rows;r++)for(let c=0;c<cols;c++){const a=r*(cols+1)+c,b=a+1,d=(r+1)*(cols+1)+c,e=d+1;indices.push(a,d,b,b,d,e);}
  const shellGeo=new THREE.BufferGeometry();shellGeo.setAttribute('position',new THREE.Float32BufferAttribute(verts,3));shellGeo.setAttribute('color',new THREE.Float32BufferAttribute(colors,3));shellGeo.setIndex(indices);shellGeo.computeVertexNormals();const shell=new THREE.Mesh(shellGeo,shellMat);face.add(shell);

  const panelCount=low?500:980,panelGeo=new THREE.BoxGeometry(1,1,.16),panelMat=new THREE.MeshStandardMaterial({color:0xffffff,metalness:.94,roughness:.34,emissive:0x3d210c,emissiveIntensity:.07}),panels=new THREE.InstancedMesh(panelGeo,panelMat,panelCount);panels.instanceColor=new THREE.InstancedBufferAttribute(new Float32Array(panelCount*3),3);
  const normal=new THREE.Vector3(),zAxis=new THREE.Vector3(0,0,1),spin=new THREE.Quaternion();
  for(let i=0;i<panelCount;i++){
    const y=rand(-2.72,2.9),nx=rand(-.98,.98),p=surface(nx,y),x=p.x,feature=(Math.abs(y-.68)<.45&&Math.abs(Math.abs(x)-.78)<.62)||(Math.abs(y+1.02)<.28&&Math.abs(x)<.92);
    normal.set(x*.15,y*.035,1).normalize();quat.setFromUnitVectors(zAxis,normal);spin.setFromAxisAngle(zAxis,rand(-Math.PI,Math.PI));quat.multiply(spin);const s=feature?rand(.018,.052):rand(.045,.145);scale.set(s*rand(1.2,2.8),s*rand(.42,1.15),rand(.055,.13));p.addScaledVector(normal,rand(.022,.072));matrix.compose(p,quat,scale);panels.setMatrixAt(i,matrix);const col=(Math.random()<.11?bronze:Math.random()<.38?bone:charcoal).clone().multiplyScalar(rand(.76,1.08));panels.setColorAt(i,col);
  }
  panels.instanceMatrix.needsUpdate=true;panels.instanceColor.needsUpdate=true;face.add(panels);

  const emberCount=low?650:1300,emberPos=new Float32Array(emberCount*3),emberBase=new Float32Array(emberCount*3);
  for(let i=0;i<emberCount;i++){const y=rand(-2.7,3),nx=rand(-.98,.98),p=surface(nx,y);p.z+=rand(.05,.18);emberPos.set([p.x,p.y,p.z],i*3);emberBase.set([p.x,p.y,p.z],i*3);}
  const emberGeo=new THREE.BufferGeometry();emberGeo.setAttribute('position',new THREE.BufferAttribute(emberPos,3));const emberMat=new THREE.PointsMaterial({color:0xffa12f,size:low?.018:.024,transparent:true,opacity:.68,blending:THREE.AdditiveBlending,depthWrite:false});const embers=new THREE.Points(emberGeo,emberMat);face.add(embers);

  function eye(side){
    const group=new THREE.Group();group.position.set(side*.79,.67,1.56);
    const socket=new THREE.Mesh(new THREE.SphereGeometry(.48,32,20),new THREE.MeshStandardMaterial({color:0x050403,metalness:1,roughness:.25}));socket.scale.set(1.35,.55,.28);group.add(socket);
    const lensMat=new THREE.MeshStandardMaterial({color:0x1b140d,metalness:.46,roughness:.3,emissive:0x763708,emissiveIntensity:.28});const lens=new THREE.Mesh(new THREE.SphereGeometry(.32,28,18),lensMat);lens.scale.set(1.15,.47,.3);lens.position.z=.18;group.add(lens);
    const iris=new THREE.Mesh(new THREE.SphereGeometry(.078,24,14),new THREE.MeshStandardMaterial({color:0x8d5e25,emissive:0xff951f,emissiveIntensity:2,metalness:.52,roughness:.2}));iris.position.z=.29;iris.scale.z=.36;group.add(iris);
    const pupil=new THREE.Mesh(new THREE.SphereGeometry(.032,18,12),new THREE.MeshBasicMaterial({color:0x010100}));pupil.position.z=.334;pupil.scale.set(1,1,.3);group.add(pupil);
    const upper=tube([new THREE.Vector3(-.57,.02,.23),new THREE.Vector3(-.26,.18,.31),new THREE.Vector3(.05,.21,.34),new THREE.Vector3(.34,.14,.3),new THREE.Vector3(.57,0,.22)],.055,0x776b5a,.11);
    const lower=tube([new THREE.Vector3(-.54,-.01,.23),new THREE.Vector3(-.22,-.13,.3),new THREE.Vector3(.08,-.15,.32),new THREE.Vector3(.36,-.1,.28),new THREE.Vector3(.54,0,.22)],.036,0x3b342b,.07);group.add(upper,lower);
    group.userData={lens,iris,pupil,lensMat};face.add(group);return group;
  }
  const leftEye=eye(-1),rightEye=eye(1);
  face.add(tube([new THREE.Vector3(-1.52,1.22,1.02),new THREE.Vector3(-1.05,1.38,1.33),new THREE.Vector3(-.53,1.27,1.48),new THREE.Vector3(-.27,1.12,1.42)],.075,0x514a40,.12));
  face.add(tube([new THREE.Vector3(.27,1.12,1.42),new THREE.Vector3(.53,1.27,1.48),new THREE.Vector3(1.05,1.38,1.33),new THREE.Vector3(1.52,1.22,1.02)],.075,0x514a40,.12));

  const noseGeo=new THREE.BufferGeometry();noseGeo.setAttribute('position',new THREE.Float32BufferAttribute([-.18,1.1,1.42,.18,1.1,1.42,-.29,-.38,1.48,.29,-.38,1.48,0,-.48,2.02,-.43,-.52,1.45,.43,-.52,1.45],3));noseGeo.setIndex([0,2,4,0,4,1,1,4,3,2,5,4,4,6,3,2,3,6,2,6,5]);noseGeo.computeVertexNormals();const nose=new THREE.Mesh(noseGeo,new THREE.MeshStandardMaterial({color:0x716655,metalness:.82,roughness:.38}));face.add(nose);
  for(const side of [-1,1]){const nostril=new THREE.Mesh(new THREE.TorusGeometry(.105,.035,8,20,Math.PI),new THREE.MeshStandardMaterial({color:0x100b07,metalness:.8,roughness:.48}));nostril.position.set(side*.19,-.47,1.83);nostril.rotation.z=side<0?0:Math.PI;face.add(nostril);}
  face.add(tube([new THREE.Vector3(-1.46,.22,1.08),new THREE.Vector3(-1.34,-.42,1.18),new THREE.Vector3(-1.08,-1.08,1.17),new THREE.Vector3(-.72,-1.58,1.08)],.055,0x5a5146,.07));
  face.add(tube([new THREE.Vector3(1.46,.22,1.08),new THREE.Vector3(1.34,-.42,1.18),new THREE.Vector3(1.08,-1.08,1.17),new THREE.Vector3(.72,-1.58,1.08)],.055,0x5a5146,.07));

  const mouth=new THREE.Group();mouth.position.set(0,-1.05,1.43);face.add(mouth);const cavity=new THREE.Mesh(new THREE.SphereGeometry(.5,28,16),new THREE.MeshStandardMaterial({color:0x080302,roughness:.6}));cavity.scale.set(1.18,.3,.2);cavity.position.z=-.02;mouth.add(cavity);
  mouth.add(tube([new THREE.Vector3(-.58,.02,.07),new THREE.Vector3(-.3,.09,.17),new THREE.Vector3(0,.04,.22),new THREE.Vector3(.3,.09,.17),new THREE.Vector3(.58,.02,.07)],.045,0x5a493d,.07));
  const teeth=new THREE.Group();for(let i=0;i<6;i++){const tooth=new THREE.Mesh(new THREE.BoxGeometry(.09,.082,.045),new THREE.MeshStandardMaterial({color:0x665b48,metalness:.44,roughness:.45,emissive:0x221307,emissiveIntensity:.05}));tooth.position.set((i-2.5)*.095,-.055,.115);teeth.add(tooth);}mouth.add(teeth);
  const jaw=new THREE.Group();mouth.add(jaw);jaw.add(tube([new THREE.Vector3(-.56,-.03,.08),new THREE.Vector3(-.28,-.115,.17),new THREE.Vector3(0,-.145,.21),new THREE.Vector3(.28,-.115,.17),new THREE.Vector3(.56,-.03,.08)],.05,0x514238,.07));
  const chin=new THREE.Mesh(new THREE.DodecahedronGeometry(.58,2),new THREE.MeshStandardMaterial({color:0x3d3a35,metalness:.88,roughness:.42}));chin.scale.set(1.16,.62,.38);chin.position.set(0,-.57,-.2);jaw.add(chin);

  const cableField=new THREE.Group();face.add(cableField);for(let i=0;i<(low?38:70);i++){const side=i%2?1:-1,start=new THREE.Vector3(side*rand(1.35,2.18),rand(-2.45,2.8),rand(.25,1.15)),end=new THREE.Vector3(side*rand(3.1,7.4),rand(-3.2,5),rand(-4.8,-.5));cableField.add(tube([start,start.clone().lerp(end,.28).add(new THREE.Vector3(side*rand(.1,.7),rand(-.7,.7),rand(-.3,.6))),start.clone().lerp(end,.62).add(new THREE.Vector3(side*rand(.1,.5),rand(-.8,.8),rand(-.4,.4))),end],rand(.009,.035),Math.random()<.18?0x8a5b26:0x34291f,Math.random()<.18?.22:.04,20));}
  const debrisCount=low?260:520,debris=new THREE.InstancedMesh(new THREE.TetrahedronGeometry(.055,0),new THREE.MeshStandardMaterial({color:0x2d2923,metalness:.94,roughness:.32,emissive:0x512b0d,emissiveIntensity:.12}),debrisCount);
  for(let i=0;i<debrisCount;i++){const angle=rand(0,Math.PI*2),radius=rand(2.25,5.8),p=new THREE.Vector3(Math.cos(angle)*radius,rand(-3.1,4.2),rand(-2.2,.7));quat.setFromEuler(new THREE.Euler(rand(0,3),rand(0,3),rand(0,3)));const s=rand(.35,2.6);matrix.compose(p,quat,new THREE.Vector3(s,s*.65,s));debris.setMatrixAt(i,matrix);}debris.instanceMatrix.needsUpdate=true;face.add(debris);

  const droneGeo=new THREE.BufferGeometry(),dronePositions=[];for(let i=0;i<(low?95:190);i++)dronePositions.push(rand(-10,10),rand(-3.5,5.4),rand(-6,1));droneGeo.setAttribute('position',new THREE.Float32BufferAttribute(dronePositions,3));const drones=new THREE.Points(droneGeo,new THREE.PointsMaterial({color:0xffaa3e,size:low?.025:.035,transparent:true,opacity:.74,blending:THREE.AdditiveBlending,depthWrite:false}));scene.add(drones);
  sky.visible=false;city.visible=false;face.visible=false;

  scene.add(new THREE.HemisphereLight(0xb8a485,0x080706,.95));const key=new THREE.DirectionalLight(0xffc995,2.35);key.position.set(-4,5,7);scene.add(key);const faceGlow=new THREE.PointLight(0xff8a20,23,18,2);faceGlow.position.set(-.4,.4,5.5);scene.add(faceGlow);const rim=new THREE.PointLight(0xffc66d,25,24,2);rim.position.set(-5.5,3,-1);scene.add(rim);const cool=new THREE.PointLight(0x6e9b8d,14,18,2);cool.position.set(5,-.2,1);scene.add(cool);const alertLight=new THREE.PointLight(0xff3d22,0,15,2);alertLight.position.set(1,0,4.5);scene.add(alertLight);
  const faceLayer=document.querySelector('#face-parallax'),faceRig=document.querySelector('#face-rig'),rigEyes=[...document.querySelectorAll('.rig-eye')],rigJaw=document.querySelector('#rig-jaw'),rigMouth=document.querySelector('.rig-mouth');
  function layoutRig(){if(!faceRig)return;const s=Math.max(innerWidth/1671,innerHeight/941),x=(innerWidth-1671*s)/2,y=(innerHeight-941*s)/2;faceRig.style.transform=`translate(${x}px,${y}px) scale(${s})`;}
  const pointer=new THREE.Vector2(),clock=new THREE.Clock(),emberAttr=emberGeo.attributes.position;let state='idle',reduced=matchMedia('(prefers-reduced-motion: reduce)').matches,active=true,gazeTarget=0,flash=0;
  addEventListener('pointermove',event=>{pointer.x=event.clientX/innerWidth*2-1;pointer.y=-(event.clientY/innerHeight*2-1);});
  addEventListener('resize',()=>{camera.aspect=innerWidth/innerHeight;camera.updateProjectionMatrix();renderer.setSize(innerWidth,innerHeight);layoutRig();});layoutRig();
  function animate(){
    requestAnimationFrame(animate);if(document.hidden||!active)return;const t=clock.getElapsedTime(),motion=reduced?0:1,work=state==='working'?1:0,speak=state==='speaking'?1:0,receiving=state==='receiving'?1:0,tool=state==='tool'?1:0,offline=state==='offline'?1:0,error=state==='error'?1:0;
    const target=gazeTarget||(state==='delegating'?.16:state==='waiting_result'?.13:0),gazeX=(target+pointer.x*.022+(work?Math.sin(t*.72)*.028:0))*motion,gazeY=(pointer.y*.015+(work?Math.cos(t*.55)*.018:0))*motion;
    const blink=motion&&Math.sin(t*.69)>.995?.12:1;
    for(const eye of [leftEye,rightEye]){eye.userData.lens.scale.y=.47*blink;eye.userData.iris.scale.y=blink;eye.userData.pupil.scale.y=blink;eye.userData.iris.position.x=gazeX;eye.userData.iris.position.y=gazeY;eye.userData.pupil.position.x=gazeX*1.22;eye.userData.pupil.position.y=gazeY*1.22;eye.userData.lensMat.emissiveIntensity=(.26+work*(.42+.18*Math.sin(t*4))+receiving*.58+tool*(.5+.36*Math.sin(t*9)))*(offline?.13:1);}
    const mouthOpen=speak*motion*(.10+.24*(.5+.5*Math.sin(t*8.2))+.045*Math.sin(t*3.3));jaw.position.y=-mouthOpen;jaw.rotation.x=-mouthOpen*.38;teeth.position.y=-mouthOpen*.18;cavity.scale.y=.34+mouthOpen*.58;
    for(const eye of rigEyes){eye.style.setProperty('--eye-x',`${gazeX*48}px`);eye.style.setProperty('--eye-y',`${gazeY*42}px`);eye.style.opacity=String((offline?.42:1)*(.78+work*.2+tool*.2));}
    if(rigJaw)rigJaw.style.setProperty('--jaw-y',`${mouthOpen*56}px`);if(rigMouth)rigMouth.style.setProperty('--mouth-opacity',String(.08+mouthOpen*2.2));
    if(faceLayer){const shiftX=motion*(gazeTarget*25+pointer.x*1.4),shiftY=motion*(-pointer.y*.7+Math.sin(t*.43)*.45),turn=motion*(gazeTarget*1.6+pointer.x*.08+Math.sin(t*.17)*.12);faceLayer.style.transform=`translate3d(${shiftX}px,${shiftY}px,0) rotate(${turn}deg) scale(${1+receiving*.003})`;faceLayer.style.opacity=offline?'.48':'1';}
    root.rotation.y=motion*(pointer.x*.012+Math.sin(t*.17)*.009+gazeTarget*.11);root.rotation.x=motion*(-pointer.y*.006+Math.sin(t*.21)*.004-receiving*.018);root.position.y=.18+motion*Math.sin(t*.43)*.018+receiving*.045;
    cableField.rotation.z=motion*Math.sin(t*.15)*.004;debris.rotation.y=motion*Math.sin(t*.12)*.018;debris.position.y=motion*Math.sin(t*.27)*.035;drones.rotation.y=motion*t*.009;drones.position.y=motion*Math.sin(t*.19)*.07;
    flash=Math.max(0,flash-.018);faceGlow.intensity=(23+work*(9+4*Math.sin(t*3.2))+speak*(4+2*Math.sin(t*8))+tool*(10+7*Math.sin(t*10))+flash*17)*(offline?.11:1);alertLight.intensity=error*(11+4*Math.sin(t*5));panelMat.emissiveIntensity=(.06+work*(.12+.08*Math.sin(t*3))+tool*.25)*(offline?.12:1);emberMat.opacity=(.4+work*(.2+.12*Math.sin(t*4))+tool*.28)*(offline?.15:1);
    if((work||tool)&&motion){for(let i=0;i<emberCount;i++){const n=i*3,y=emberBase[n+1];emberAttr.array[n+2]=emberBase[n+2]+Math.sin(y*3.4-t*(tool?8:5))*.024;}emberAttr.needsUpdate=true;}else if(emberAttr.array[2]!==emberBase[2]){emberAttr.array.set(emberBase);emberAttr.needsUpdate=true;}
    renderer.render(scene,camera);
  }
  animate();
  return {setState(next){state=next;if(next==='complete')flash=1;},setReduced(value){reduced=value;},setGaze(value){gazeTarget=value;},setActive(value){active=value;if(value)renderer.render(scene,camera);},quality:low?'Úsporná':'Vysoká'};
}

const ui={
  film:document.querySelector('#film-view'),work:document.querySelector('#work-view'),faceState:document.querySelector('#face-state'),faceTask:document.querySelector('#face-task'),faceSignal:document.querySelector('#face-signal'),header:document.querySelector('#header-status'),liveDot:document.querySelector('#live-dot'),snapshotAge:document.querySelector('#snapshot-age'),agentCount:document.querySelector('#agent-count'),requestCount:document.querySelector('#request-count'),costTotal:document.querySelector('#cost-total'),attention:document.querySelector('#attention-action'),demo:document.querySelector('#demo-panel'),demoStates:document.querySelector('#demo-states'),detail:document.querySelector('#agent-detail'),backdrop:document.querySelector('#drawer-backdrop')
};
document.querySelector('#server-fallback-details').removeAttribute('open');
const face=createFaceController(document.querySelector('#machine-scene'),document.querySelector('#webgl-fallback'));
let liveData=null,demo=false,demoState='idle',view='film',selectedAgent=null,reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
document.documentElement.dataset.motion=reduced?'reduced':'full';if(face)face.setReduced(reduced);

function statusLabel(status){return ({idle:'Čeká',working:'Pracuje',blocked:'Blokováno',done:'Hotovo',unknown:'Neznámé',offline:'Odpojeno'})[status]||'Neznámé';}
function source(profile,kind){return liveData?.sources?.find(item=>item.profile===profile&&item.kind===kind);}
function agents(){return ['majak','quantlab'].flatMap(profile=>{const s=source(profile,'herdr');return s?.status==='available'?s.rows.map(row=>({...row,profile})):[];});}
function profileStats(profile){
  const router=source(profile,'router'),rows=router?.status==='available'?router.rows:[];
  const sum=key=>rows.reduce((total,row)=>row[key]==null?total:total+row[key],0);
  const knownCost=rows.length&&rows.every(row=>row.cost_microusd!=null);
  return {requests:sum('requests'),input:sum('input_tokens'),output:sum('output_tokens'),cost:knownCost?sum('cost_microusd'):null,fallbacks:sum('fallback_count'),models:[...new Set(rows.map(row=>row.actual_model).filter(Boolean))],providers:[...new Set(rows.map(row=>row.provider).filter(Boolean))],router};
}
function effectiveState(){
  if(demo)return demoState;
  if(!liveData)return 'offline';
  const all=agents(),hermes=all.filter(item=>item.agent.endsWith('-hermes'));
  if(liveData.sources.some(item=>item.kind==='herdr'&&item.status==='unavailable'))return hermes.length?'error':'offline';
  if(hermes.some(item=>item.status==='blocked'))return 'waiting_user';
  if(hermes.some(item=>item.status==='working'))return 'working';
  if(hermes.some(item=>item.status==='idle'))return 'idle';
  if(hermes.length&&hermes.every(item=>item.status==='done'))return 'complete';
  return hermes.length?'idle':'offline';
}
function faceCopy(state){
  if(demo)return STATE_META[state].copy;
  if(state==='working'){const names=agents().filter(a=>a.status==='working').map(a=>a.agent);return names.length?`Pracují: ${names.join(', ')}. Přesný úkol není ve zdroji.`:STATE_META[state].copy;}
  if(state==='waiting_user'){const names=agents().filter(a=>a.status==='blocked').map(a=>a.agent);return `Zkontrolujte: ${names.join(', ')||'blokovaného agenta'}.`}
  if(state==='error'){const failed=liveData.sources.filter(s=>s.status==='unavailable'&&s.reason!=='not_configured');return failed.length?`${failed.map(s=>`${s.profile}/${s.kind}: ${s.reason}`).join(' · ')}`:'Živý stav jednoho projektu není dostupný.';}
  return STATE_META[state].copy;
}
function toneColor(tone){return tone==='green'?'#6adf9a':tone==='red'?'#ff7159':tone==='muted'?'#716d66':'#e49a34';}
function renderFace(){const state=effectiveState(),meta=STATE_META[state];ui.faceState.textContent=meta.label;ui.faceTask.textContent=faceCopy(state);const color=toneColor(meta.tone);ui.faceSignal.style.background=color;ui.faceSignal.style.color=color;ui.faceSignal.style.boxShadow=`0 0 13px ${color}`;if(face)face.setState(state);ui.attention.hidden=!['waiting_user','error'].includes(state);}
function agentButton(item){const kind=item.agent.endsWith('-hermes')?'hermes':'codex',role=kind==='hermes'?'Koordinátor':'Vývojový agent';return `<button class="agent-node" data-agent="${item.agent}" data-state="${item.status}"><span class="agent-orb" aria-hidden="true"></span><span class="agent-copy"><b>${item.agent}</b><small>${role}</small></span><span class="state-chip">${statusLabel(item.status)}</span></button>`;}
function unavailableAgent(profile,kind){const name=`${profile}-${kind}`;return `<button class="agent-node" data-agent="${name}" data-state="offline"><span class="agent-orb" aria-hidden="true"></span><span class="agent-copy"><b>${name}</b><small>Stav není dostupný</small></span><span class="state-chip">Odpojeno</span></button>`;}
function renderProjects(){
  for(const profile of ['majak','quantlab']){const list=document.querySelector(`#${profile}-agents`),items=agents().filter(a=>a.profile===profile).sort((a,b)=>Number(b.agent.endsWith('-hermes'))-Number(a.agent.endsWith('-hermes')));list.innerHTML=items.length?items.map(agentButton).join(''):unavailableAgent(profile,'hermes')+unavailableAgent(profile,'codex');const state=items.some(a=>a.status==='working')?'pracuje':items.some(a=>a.status==='blocked')?'zásah':'klid';document.querySelector(`#${profile}-project-state`).textContent=state;}
  document.querySelectorAll('[data-agent]').forEach(button=>button.addEventListener('click',()=>openDetail(button.dataset.agent)));
  renderLinks();
}
function renderLinks(){const layer=document.querySelector('#link-layer');layer.replaceChildren();const nodes=[...document.querySelectorAll('.agent-node')];if(view!=='film')return;const rect=ui.film.getBoundingClientRect(),cx=rect.width/2,cy=rect.height*.49;nodes.forEach(node=>{const box=node.getBoundingClientRect(),x=box.left+box.width/2-rect.left,y=box.top+box.height/2-rect.top,dx=x-cx,dy=y-cy,length=Math.hypot(dx,dy),line=document.createElement('span');line.className='data-link';line.dataset.target=node.dataset.agent;line.style.setProperty('--length',`${length}px`);line.style.setProperty('--angle',`${Math.atan2(dy,dx)}rad`);if(demo&&['delegating','waiting_result'].includes(demoState)&&(!selectedAgent||node.dataset.agent===selectedAgent)){line.classList.add('is-active');if(demoState==='waiting_result')line.classList.add('is-return');}if(demo&&demoState==='tool'&&node.dataset.agent.endsWith('-hermes'))line.classList.add('is-tool');layer.append(line);});}
function renderTelemetry(){
  if(!liveData){ui.snapshotAge.textContent='Nedostupné';ui.agentCount.textContent='0 / neznámé';ui.requestCount.textContent='Nedostupné';ui.costTotal.textContent='Neznámé';return;}
  const stats=['majak','quantlab'].map(profileStats),requests=stats.reduce((n,s)=>n+s.requests,0),known=stats.every(s=>s.cost!=null),cost=known?stats.reduce((n,s)=>n+s.cost,0):null;
  ui.snapshotAge.textContent=ago(liveData.generated_at);ui.agentCount.textContent=String(agents().length);ui.requestCount.textContent=fmt.format(requests);ui.costTotal.textContent=money(cost);document.querySelector('#work-freshness').textContent=`Snapshot před ${ago(liveData.generated_at)}`;
}
function renderWork(){
  const grid=document.querySelector('#project-grid');grid.innerHTML=['majak','quantlab'].map(profile=>{const items=agents().filter(a=>a.profile===profile),stats=profileStats(profile),models=stats.models.length?stats.models.join(', '):'Nedostupné';return `<article class="work-project"><h2>${profile.toUpperCase()}</h2>${items.length?items.map(a=>`<button class="work-agent" data-agent="${a.agent}"><b>${a.agent}</b><span>${statusLabel(a.status)}</span><small>${AGENT_ROLES[a.agent.endsWith('-hermes')?'hermes':'codex']}</small></button>`).join(''):'<p>Živý stav agentů není dostupný.</p>'}<p class="work-models">Modely profilu: ${models}</p><div class="work-totals"><div><span>Požadavky</span><b>${fmt.format(stats.requests)}</b></div><div><span>Tokeny vstup/výstup</span><b>${fmt.format(stats.input)} / ${fmt.format(stats.output)}</b></div><div><span>Náklady / fallbacky</span><b>${money(stats.cost)} · ${fmt.format(stats.fallbacks)}</b></div></div></article>`;}).join('');
  grid.querySelectorAll('[data-agent]').forEach(button=>button.addEventListener('click',()=>openDetail(button.dataset.agent)));
  const sources=document.querySelector('#source-grid');sources.innerHTML=(liveData?.sources||[]).map(s=>`<article class="source-card" data-status="${s.status}"><b>${s.profile} / ${s.kind}</b><span>${s.status} · ${s.reason}</span><span>${s.status==='available'?`${s.rows.length} záznamů`:'bez dat'}</span></article>`).join('');
  const attention=document.querySelector('#attention-summary'),problems=(liveData?.sources||[]).filter(s=>s.status==='unavailable'&&s.reason!=='not_configured');attention.hidden=!problems.length;attention.textContent=problems.length?`Vyžaduje kontrolu: ${problems.map(s=>`${s.profile}/${s.kind} (${s.reason})`).join(', ')}`:'';
}
function openDetail(name){
  selectedAgent=name;const profile=name.startsWith('majak-')?'majak':'quantlab',kind=name.endsWith('-hermes')?'hermes':'codex',item=agents().find(a=>a.agent===name),stats=profileStats(profile),herdr=source(profile,'herdr');
  document.querySelector('#detail-profile').textContent=profile.toUpperCase();document.querySelector('#detail-title').textContent=name;document.querySelector('#detail-role').textContent=AGENT_ROLES[kind];document.querySelector('#detail-status').textContent=`Stav: ${item?statusLabel(item.status):'Odpojeno / neznámé'}`;
  const model=stats.models.length?stats.models.join(', '):'Nedostupné v aktuálním snapshotu',provider=stats.providers.length?stats.providers.join(', '):'Nedostupné';document.querySelector('#detail-metrics').innerHTML=`<div><dt>Aktuální úkol</dt><dd>Nedostupné v datovém kontraktu</dd></div><div><dt>Modely profilu</dt><dd>${model}</dd></div><div><dt>Provider / fallbacky</dt><dd>${provider} · ${fmt.format(stats.fallbacks)}</dd></div><div><dt>Požadavky / náklady</dt><dd>${fmt.format(stats.requests)} · ${money(stats.cost)}</dd></div>`;
  const events=[];if(item)events.push(`Herdr hlásí stav „${item.status}“ · pozorováno před ${ago(herdr?.observed_at)}`);if(stats.router?.status==='available')events.push(`Router: ${stats.requests} požadavků · data před ${ago(stats.router.data_at)}`);if(!stats.models.length)events.push('Přesné přiřazení modelu agentovi zdroj neposkytuje.');if(!events.length)events.push('Žádné ověřené události nejsou dostupné.');document.querySelector('#detail-events').innerHTML=events.map(e=>`<li>${e}</li>`).join('');
  ui.detail.hidden=false;ui.backdrop.hidden=false;document.body.style.overflow='hidden';ui.detail.querySelector('#detail-close').focus();if(face)face.setGaze(profile==='majak'?-0.13:.13);renderLinks();
}
function closeDetail(){ui.detail.hidden=true;ui.backdrop.hidden=true;document.body.style.overflow='';if(face)face.setGaze(0);}
function applyView(next){view=next;ui.film.hidden=next!=='film';ui.work.hidden=next!=='work';document.querySelectorAll('[data-view]').forEach(button=>{const active=button.dataset.view===next;button.classList.toggle('is-active',active);button.setAttribute('aria-pressed',String(active));});if(face)face.setActive(next==='film');requestAnimationFrame(renderLinks);}
function applyDemoState(next){demoState=next;document.querySelectorAll('[data-demo-state]').forEach(button=>button.classList.toggle('is-active',button.dataset.demoState===next));renderFace();renderLinks();}
function buildDemo(){for(const [key,value] of Object.entries(STATE_META)){const button=document.createElement('button');button.type='button';button.dataset.demoState=key;button.textContent=value.label;button.addEventListener('click',()=>applyDemoState(key));ui.demoStates.append(button);}}
async function refresh(){
  if(demo)return;
  try{const response=await fetch('/agent-platform/api/v1/overview',{cache:'no-store',headers:{Accept:'application/json'}});if(!response.ok)throw new Error(`HTTP ${response.status}`);liveData=await response.json();ui.header.textContent=`Živá data · ${ago(liveData.generated_at)}`;ui.liveDot.style.background='#6adf9a';ui.liveDot.style.color='#6adf9a';renderProjects();renderTelemetry();renderWork();renderFace();}
  catch(error){liveData=null;ui.header.textContent='Živá data nejsou dostupná';ui.liveDot.style.background='#ff7159';ui.liveDot.style.color='#ff7159';renderProjects();renderTelemetry();renderWork();renderFace();}
}

buildDemo();document.querySelectorAll('[data-view]').forEach(button=>button.addEventListener('click',()=>applyView(button.dataset.view)));
document.querySelector('#demo-toggle').addEventListener('click',event=>{demo=!demo;document.documentElement.dataset.mode=demo?'demo':'live';event.currentTarget.setAttribute('aria-pressed',String(demo));event.currentTarget.textContent=demo?'Ukončit demo':'Demo režim';ui.demo.hidden=!demo;if(demo){applyDemoState('idle');ui.header.textContent='DEMO · syntetické stavy';ui.liveDot.style.background='#e49a34';}else refresh();});
document.querySelector('#motion-toggle').addEventListener('click',event=>{reduced=!reduced;document.documentElement.dataset.motion=reduced?'reduced':'full';event.currentTarget.setAttribute('aria-pressed',String(reduced));event.currentTarget.textContent=reduced?'Povolit pohyb':'Omezit pohyb';if(face)face.setReduced(reduced);});
document.querySelector('#detail-close').addEventListener('click',closeDetail);ui.backdrop.addEventListener('click',closeDetail);document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!ui.detail.hidden)closeDetail();});ui.attention.addEventListener('click',()=>{const target=agents().find(a=>a.status==='blocked')||agents()[0];if(target)openDetail(target.agent);else applyView('work');});
document.querySelectorAll('[data-project-toggle]').forEach(button=>button.addEventListener('click',()=>{const list=button.closest('.project').querySelector('.agent-list'),expanded=button.getAttribute('aria-expanded')==='true';button.setAttribute('aria-expanded',String(!expanded));list.hidden=expanded;renderLinks();}));
addEventListener('resize',()=>requestAnimationFrame(renderLinks));document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
applyView(face?'film':'work');refresh();setInterval(refresh,30000);
