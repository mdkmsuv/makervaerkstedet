import * as THREE from 'three';
import { STLLoader } from 'three/addons/loaders/STLLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { validateSTL } from './stl-validation.mjs';

let renderer, scene, camera, controls, mesh, observer;
function render(){if(renderer&&scene&&camera)renderer.render(scene,camera)}
window.modelPreview={
  validate(buffer){return validateSTL(buffer).size;},
  load(buffer, colour='#c62b2f'){
    validateSTL(buffer);
    const geometry=new STLLoader().parse(buffer);
    geometry.computeBoundingBox();
    const size=new THREE.Vector3();geometry.boundingBox.getSize(size);
    if(!Number.isFinite(size.length())||size.length()===0){geometry.dispose();throw new Error('Modellen kunne ikke vises. Kontrollér STL-filen.')}
    geometry.center();geometry.computeVertexNormals();
    const holder=document.getElementById('model-canvas');
    if(!renderer){
      renderer=new THREE.WebGLRenderer({antialias:true,alpha:true});renderer.setPixelRatio(Math.min(devicePixelRatio,2));holder.replaceChildren(renderer.domElement);
      scene=new THREE.Scene();camera=new THREE.PerspectiveCamera(35,1,.01,10000);camera.up.set(0,0,1);
      scene.add(new THREE.HemisphereLight(0xffffff,0x77716a,2.8));
      const key=new THREE.DirectionalLight(0xffffff,3);key.position.set(-100,-150,250);scene.add(key);
      const fill=new THREE.DirectionalLight(0xfff5e8,1.5);fill.position.set(200,100,50);scene.add(fill);
      controls=new OrbitControls(camera,renderer.domElement);controls.enablePan=false;controls.addEventListener('change',render);
      observer=new ResizeObserver(()=>{const w=holder.clientWidth,h=holder.clientHeight;if(w&&h){renderer.setSize(w,h);camera.aspect=w/h;camera.updateProjectionMatrix();render()}});observer.observe(holder);
    }
    if(mesh){scene.remove(mesh);mesh.geometry.dispose();mesh.material.dispose()}
    mesh=new THREE.Mesh(geometry,new THREE.MeshStandardMaterial({color:colour,roughness:.58,metalness:.03,side:THREE.DoubleSide}));scene.add(mesh);
    const radius=Math.max(size.x,size.y,size.z)*2.25;
    camera.position.set(radius,-radius,radius*.8);controls.target.set(0,0,0);camera.near=Math.max(.001,radius/1000);camera.far=radius*20;camera.updateProjectionMatrix();controls.update();render();
    return [size.x,size.y,size.z];
  },
  colour(hex){if(mesh){mesh.material.color.set(hex);render()}},
  clear(){if(mesh){scene.remove(mesh);mesh.geometry.dispose();mesh.material.dispose();mesh=null;render()}}
};
