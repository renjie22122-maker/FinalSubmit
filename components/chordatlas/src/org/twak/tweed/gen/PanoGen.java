package org.twak.tweed.gen;

import java.awt.Color;
import java.awt.Dimension;
import java.awt.event.ActionEvent;
import java.awt.event.ActionListener;
import java.awt.image.BufferedImage;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.Random;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.stream.Collectors;

import javax.swing.JButton;
import javax.swing.JComponent;
import javax.swing.JLabel;
import javax.swing.JOptionPane;
import javax.swing.JPanel;
import javax.swing.JScrollPane;
import javax.swing.SwingUtilities;
import javax.swing.JTextArea;
import javax.vecmath.Point3d;
import javax.vecmath.Vector3d;

import org.geotools.referencing.CRS;
import org.geotools.referencing.crs.DefaultGeocentricCRS;
import org.opengis.referencing.FactoryException;
import org.opengis.referencing.NoSuchAuthorityCodeException;
import org.opengis.referencing.operation.MathTransform;
import org.opengis.referencing.operation.TransformException;
import org.twak.siteplan.jme.Jme3z;
import org.twak.tweed.ClickMe;
import org.twak.tweed.EventMoveHandle;
import org.twak.tweed.IDumpObjs;
import org.twak.tweed.Tweed;
import org.twak.tweed.TweedSettings;
import org.twak.tweed.tools.FacadeTool;
import org.twak.tweed.tools.PlaneTool;
import org.twak.utils.Filez;
import org.twak.utils.Mathz;
import org.twak.utils.geom.ObjDump;
import org.twak.utils.ui.ListDownLayout;

import com.jme3.material.Material;
import com.jme3.math.ColorRGBA;
import com.jme3.math.Vector2f;
import com.jme3.math.Vector3f;
import com.jme3.math.Vector4f;
import com.jme3.scene.Geometry;
import com.jme3.scene.Mesh;
import com.jme3.scene.Mesh.Mode;
import com.jme3.scene.Spatial;
import com.jme3.scene.VertexBuffer;
import com.jme3.scene.shape.Box;
import com.jme3.util.BufferUtils;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.thoughtworks.xstream.XStream;

public class PanoGen extends Gen implements IDumpObjs, ICanSave {

	private static final ObjectMapper MANIFEST_MAPPER = new ObjectMapper();

	static final class WorkspaceLocalFrame {
		final double originLat, originLon;

		WorkspaceLocalFrame( double originLat, double originLon ) {
			this.originLat = originLat;
			this.originLon = originLon;
		}
	}
	
	File folder;
	public transient volatile List<Pano> panos = new CopyOnWriteArrayList<>();
	
	public String sourceCRS;
	/** Use the same local metre frame as Sat3DGen's mesh pipeline. */
	public boolean meshPipelineLocalCoordinates = false;
	public double localOriginLat = Double.NaN;
	public double localOriginLon = Double.NaN;
	/** Set on the Swing thread and consumed on the jME calculation thread. */
	private transient volatile boolean forceFolderRescan = false;
	
	transient Pano selectedPano = null;
	transient JPanel ui = new JPanel();
	
	public  transient List<ImagePlaneGen> planes = new ArrayList<>();
	
	public PanoGen() {}
	
	public PanoGen(File folder, Tweed tweed, String sourceCRS ) {
		super ("panos "+folder.getName(), tweed);
		this.folder = folder;
		this.sourceCRS = sourceCRS;
		configureFromWorkspaceManifest();
	}

	static WorkspaceLocalFrame detectWorkspaceLocalFrame( File manifest ) {
		if ( manifest == null || !manifest.isFile() )
			return null;
		try {
			JsonNode root = MANIFEST_MAPPER.readTree( manifest );
			JsonNode frame = root == null ? null : root.get( "frame" );
			JsonNode axes = frame == null ? null : frame.get( "axes" );
			if ( frame == null || !frame.isObject() || axes == null || !axes.isObject() )
				throw new IOException( "frame.axes is missing" );
			if ( !"east".equals( axes.path( "x" ).asText() ) ||
					!"up".equals( axes.path( "y" ).asText() ) ||
					!"south".equals( axes.path( "z" ).asText() ) )
				throw new IOException( "frame.axes must be x=east, y=up, z=south" );

			JsonNode latNode = frame.get( "origin_lat" ), lonNode = frame.get( "origin_lon" );
			if ( latNode == null || !latNode.isNumber() || lonNode == null || !lonNode.isNumber() )
				throw new IOException( "frame origin_lat/origin_lon must be numeric" );
			double lat = latNode.asDouble(), lon = lonNode.asDouble();
			if ( !Double.isFinite( lat ) || !Double.isFinite( lon ) || lat < -90 || lat > 90 || lon < -180 || lon > 180 )
				throw new IOException( "frame origin is outside WGS84 bounds" );
			return new WorkspaceLocalFrame( lat, lon );
		} catch ( IOException ex ) {
			System.err.println( "PanoGen cannot use myProject local coordinates from " + manifest +
					": " + ex.getMessage() + "; falling back to original geographic coordinates." );
			return null;
		}
	}

	void configureFromWorkspaceManifest() {
		if ( sourceCRS == null || !Tweed.LAT_LONG.equalsIgnoreCase( sourceCRS ) ) {
			meshPipelineLocalCoordinates = false;
			localOriginLat = Double.NaN;
			localOriginLon = Double.NaN;
			return;
		}
		if ( Tweed.DATA == null )
			return;

		// Re-evaluate persisted layers against the workspace they are being loaded
		// into.  This migrates old PanoGen XML and prevents a stale origin from a
		// copied project surviving a missing or invalid manifest.
		meshPipelineLocalCoordinates = false;
		localOriginLat = Double.NaN;
		localOriginLon = Double.NaN;
		File manifest = new File( Tweed.DATA, "manifest.json" );
		WorkspaceLocalFrame local = detectWorkspaceLocalFrame( manifest );
		if ( local == null ) {
			if ( !manifest.isFile() )
				System.err.println( "PanoGen found no workspace manifest at " + manifest +
						"; using original geographic coordinates." );
			return;
		}
		meshPipelineLocalCoordinates = true;
		localOriginLat = local.originLat;
		localOriginLon = local.originLon;
		System.out.println( "PanoGen using myProject local frame origin " + localOriginLat + "," + localOriginLon );
	}
		
	public PanoGen( Tweed tweed ) {
		super ("render progress", tweed);
		this.tweed = tweed;
		this.folder = null;
	}
	
	@Override
	public void calculate( ) {
		
		
		if ( folder != null ) {
			File absFolder = Tweed.toWorkspace( folder );

			if ( !absFolder.exists() )
				throw new Error( "File not found " + this.folder );
		}
		
		for (Spatial s : gNode.getChildren())
			s.removeFromParent();
				
		createPanoGens();
		List<Pano> currentPanos = panos;
		
		Random randy = new Random (0xdeadbeef);
		
		for (Pano p : currentPanos) {
			if (p.geom == null) {
				
				Box box1 = new Box(1f, 1f, 1f);
				p.geom = new Geometry("Box", box1);

//				p.geom.setUserData(Gen.class.getSimpleName(), new Object[]{this});
				
				p.geom.setUserData(EventMoveHandle.class.getSimpleName(), new Object[] { new EventMoveHandle() {
					@Override
					public void posChanged() {
						p.location = new Vector3d( Jme3z.from ( p.geom.getLocalTranslation() ) );
						calculate();
					}
				} });
				
//				Material mat1 = new Material(tweed.getAssetManager(), "Common/MatDefs/Misc/Unshaded.j3md");
				
				ColorRGBA col = new ColorRGBA( 
						color.getRed()   * (0.2f + randy.nextFloat()*0.8f) / 255f, 
						color.getGreen() * (0.2f + randy.nextFloat()*0.8f) / 255f, 
						color.getBlue()  * (0.2f + randy.nextFloat()*0.8f) / 255f, 1f );
				
				Material mat = new Material( tweed.getAssetManager(), "Common/MatDefs/Light/Lighting.j3md" );
				mat.setColor( "Diffuse", col );
				mat.setColor( "Ambient", col );
				mat.setBoolean( "UseMaterialColors", true );
				
				p.geom.setMaterial(mat);
			}
			
			p.geom.setLocalTranslation( (float) p.location.x, (float) p.location.y, (float) p.location.z);
			p.geom.setLocalRotation( p.geomRot ); 
			
			p.geom.setUserData( ClickMe.class.getSimpleName(), new Object[] { new ClickMe() {
				@Override
				public void clicked( Object data ) {
					tweed.frame.setSelected( PanoGen.this );
					selected(p);
				}
			} } );
	        
	        gNode.attachChild(p.geom);
		}
		
		for (ImagePlaneGen ipg : planes) {
			ipg.calculate();
			gNode.attachChild( ipg.gNode );
		}

		super.calculate();
	}
	
	protected void createPanoGens() {
		File meta = getMetaFile();
		boolean scanFolder = forceFolderRescan;
		forceFolderRescan = false;
		List<Pano> loaded = null;
		
		if ( !scanFolder && meta.exists() ) 
		{
			try {
				loaded = new ArrayList<>( (List<Pano>) new XStream().fromXML( meta ) );
			} catch ( Throwable th ) {
				th.printStackTrace();
			}
		}
		
		
		if (loaded == null || loaded.isEmpty()) 
		{
			loaded = new ArrayList<>();
			
			File[] files = Tweed.toWorkspace( folder ).listFiles();
			if ( files == null )
				files = new File[ 0 ];
			Arrays.sort( files );
			for ( File f : files ) {
				String extn = Filez.getExtn( f.getName() );
				if ( f.isFile() && ( extn.equalsIgnoreCase( "jpg" ) || extn.equalsIgnoreCase( "png" ) ) )
					createPanoGen( f, loaded );
			}

			loaded.removeIf( p -> p.rx == 0 && Math.abs( p.rz - Mathz.TwoPI ) < 1e-6 );
			
			try {
				
				
				for ( Pano p : loaded ) {
					if (p.orig.isAbsolute())
						p.orig = tweed.makeWorkspaceRelative( p.orig );
				}
				
				File temporary = new File( meta.getParentFile(), meta.getName() + ".part" );
				try ( FileOutputStream stream = new FileOutputStream( temporary ) ) {
					new XStream().toXML( loaded, stream );
				}
				try {
					Files.move( temporary.toPath(), meta.toPath(),
							StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE );
				} catch ( AtomicMoveNotSupportedException ex ) {
					Files.move( temporary.toPath(), meta.toPath(), StandardCopyOption.REPLACE_EXISTING );
				}
				
			} catch ( IOException e ) {
				e.printStackTrace();
			}
		}
		else
			loaded.removeIf( p -> p.rx == 0 && Math.abs( p.rz - Mathz.TwoPI ) < 1e-6 );

		panos = Collections.unmodifiableList( new ArrayList<>( loaded ) );
	}

	/** Rebuild the in-memory panorama list and panos.xml from the current folder. */
	public void rescan() {
		if ( folder == null )
			return;
		forceFolderRescan = true;
		calculateOnJmeThread();
	}

	private File getMetaFile() {
		return Tweed.toWorkspace( new File( folder, "panos.xml" ) );
	}

	private void createPanoGen( File f, List<Pano> results ) {
		Pano pano = createPanoGen( f, sourceCRS, meshPipelineLocalCoordinates,
				localOriginLat, localOriginLon );
		if ( pano != null )
			results.add( pano );
	}
	
	public static Pano createPanoGen( File f, String sourceCRS ) {
		return createPanoGen( f, sourceCRS, false, Double.NaN, Double.NaN );
	}

	public static Pano createPanoGen( File f, String sourceCRS,
			boolean meshPipelineLocalCoordinates, double localOriginLat, double localOriginLon ) {
		String name = f.getName().substring( 0, f.getName().length() - 4 );
		try
		{
			String[] sVals = name.split( "[_]", 10 );
			
			if (sVals.length < 6)
				return null;
			
			List<Double> pos = Arrays.asList( Arrays.copyOfRange( sVals, 0, 6 ) )
					.stream().map( z -> Double.parseDouble( z ) ).collect( Collectors.toList() );
			
			if ( meshPipelineLocalCoordinates ) {
				if ( !Double.isFinite( localOriginLat ) || !Double.isFinite( localOriginLon ) )
					throw new IllegalArgumentException( "local panorama origin must be finite" );
				double lat = pos.get( 0 ), lon = pos.get( 1 );
				double x = ( lon - localOriginLon ) * 111320d * Math.cos( Math.toRadians( localOriginLat ) );
				double z = -( lat - localOriginLat ) * 111320d;
				return new Pano( name, new Vector3d( x, 2.5f, z ),
						pos.get( 3 ).floatValue() + 180,
						pos.get( 4 ).floatValue(), pos.get( 5 ).floatValue() );
			}

			double[] trans = new double[] { pos.get( 0 ), pos.get( 1 ), 0 };
			double[] north = new double[] { pos.get( 0 ), pos.get( 1 ) + 1e-6, 0 };
			// two part transform to align heights - geoid for 4326 is different to 27700
			
			MathTransform latLong2Country = CRS.findMathTransform( 
					CRS.decode( sourceCRS ), 
					Tweed.kludgeCMS.get(TweedSettings.settings.gmlCoordSystem),
					true );
			
			
			latLong2Country.transform( trans, 0, trans, 0, 1 );
			latLong2Country.transform( north, 0, north, 0, 1 );
			
			if (TweedSettings.settings.gmlCoordSystem.equals ("EPSG:3042") ) { /* madrid?! */
				System.out.println("******* dirty hack in place for flipped CS");
				double tmp = trans[0];
				trans[0] = trans[1];
				trans[1] = tmp;
			}
			
			MathTransform country2Cartesian = CRS.findMathTransform( Tweed.kludgeCMS.get( TweedSettings.settings.gmlCoordSystem ),  DefaultGeocentricCRS.CARTESIAN, true );
			country2Cartesian.transform( trans, 0, trans, 0, 1 );
			country2Cartesian.transform( north, 0, north, 0, 1 );

			{
				Point3d tmp = new Point3d(trans);
				TweedSettings.settings.toOrigin.transform( tmp );
				tmp.get( trans );
				
				tmp = new Point3d(north);
				TweedSettings.settings.toOrigin.transform( tmp );
				tmp.get( north );
			}
			
			if (TweedSettings.settings.gmlCoordSystem.equals ("EPSG:2062") ) { // oviedo :(
				trans[2] -= 258;
				north[2] -= 258;
				
				trans[0] += 3;
				trans[0] += 3;
			}
			
			Vector3d location = new Vector3d( 
					trans[ 0 ], 
					2.5f /* camera height above floor */,
					trans[ 2 ] );
			
			
//			{
//				Vector3d west = new Vector3d( (float)( trans[ 0 ] - north[ 0 ]), 0f, (float)(north [ 2 ] - trans [ 2 ] ) ); 
//				west.scale( 0.6f / west.length() );
//				location.add( west );
//			}
			
//			System.out.println( "pano@ " + location );
		
			return new Pano ( name, location, 
				   (pos.get( 3 ).floatValue()+180),// + 360 - (toNorth * 180 /FastMath.PI ) ) % 360, 
					pos.get( 4 ).floatValue(), 
					pos.get( 5 ).floatValue() );
			
		} catch ( IndexOutOfBoundsException e ) {
			e.printStackTrace();
		} catch ( NoSuchAuthorityCodeException e ) {
			e.printStackTrace();
		} catch ( FactoryException e ) {
			e.printStackTrace();
		} catch ( TransformException e ) {
			e.printStackTrace();
		}
		return null;
	}
	
	/**
	 * The original Mosaic/panoscraper endpoint is no longer a supported Google
	 * download path. Keep this public method as a compatibility-safe help action
	 * instead of silently calling the obsolete downloader or renaming todo.list.
	 */
	@Deprecated
	public void downloadPanos() {
		showStreetViewImportInstructions();
	}

	private static String powershellQuote( File file ) {
		return "'" + file.getAbsolutePath().replace( "'", "''" ) + "'";
	}

	private void showStreetViewImportInstructions() {
		File panoFolder = Tweed.toWorkspace( folder );
		File todo = new File( panoFolder, TO_DOWNLOAD );
		File launcher = new File( new File( System.getProperty( "user.dir", "." ) ),
				"bridge" + File.separator + "scripts" + File.separator + "myproject.ps1" );
		String launcherText = launcher.isFile() ? powershellQuote( launcher )
				: "'<myProject>\\bridge\\scripts\\myproject.ps1'";
		String common = "& " + launcherText + " import-streetview-panos --coordinate-mode myproject-local"
				+ " --todo " + powershellQuote( todo ) + " --output " + powershellQuote( panoFolder );
		String message = "The legacy ChordAtlas panorama downloader is disabled; it uses an obsolete Google endpoint.\n\n"
				+ "In a PowerShell where GOOGLE_MAPS_API_KEY is set, run one guarded sample:\n"
				+ common + "\n\n"
				+ "Inspect the generated sample JPEG, then run the remaining records:\n"
				+ common + " --all --sample-approved\n\n"
				+ "This keeps todo.list unchanged. Return here and click 'refresh panoramas' when the import finishes.";
		JTextArea text = new JTextArea( message, 14, 100 );
		text.setEditable( false );
		text.setCaretPosition( 0 );
		JOptionPane.showMessageDialog( tweed.frame(), new JScrollPane( text ),
				"Google Static Street View import", JOptionPane.INFORMATION_MESSAGE );
	}
	
	static final String TO_DOWNLOAD = "todo.list", DOWNLOADED = "done.list";
	
	@Override
	public JComponent getUI() {
		
		ui.removeAll();
		
		ui.setLayout( new ListDownLayout() );
		ui.add(new JLabel(panos.size() +" panoramas"));
		
		if ( folder != null ) {
			ui.add( new JLabel( meshPipelineLocalCoordinates
					? "coordinates: myProject local (X east / Z south)"
					: "coordinates: original geographic" ) );
			JButton refresh = new JButton( "refresh panoramas" );
			refresh.addActionListener( e -> {
				rescan();
				SwingUtilities.invokeLater( () -> JOptionPane.showMessageDialog( tweed.frame(),
						"Panorama rescan queued. Re-select this layer to see the updated count." ) );
			} );
			ui.add( refresh );

			File absFolder = new File( Tweed.toWorkspace( folder ), TO_DOWNLOAD );

			if ( absFolder.exists() ) {
				JButton download = new JButton( "Street View import instructions" );
				download.setToolTipText( "Uses the guarded myProject Google Static Street View importer" );
				download.addActionListener( e -> downloadPanos() );
				ui.add( download );
			}
		}
		
		JButton align = new JButton("facade tool");
		align.addActionListener( e -> tweed.setTool(new FacadeTool(tweed)) );
		ui.add( align );
		
		JButton plane = new JButton("plane tool");
		plane.addActionListener( e -> tweed.setTool(new PlaneTool(tweed)) );
		ui.add( plane );
				
		return ui;
	}

	public void selected( Pano p ) {
		
		ui.removeAll();
		ui.setLayout( new ListDownLayout() );
		
		selectedPano = p;
		
		JTextArea name = new JTextArea(  p.orig.getName() );
		name.setLineWrap( true );
		name.setPreferredSize( new Dimension( 600,100) );
		
		JButton recalc = new JButton("reset");
		recalc.addActionListener( new ActionListener() {
			
			@Override
			public void actionPerformed( ActionEvent e ) {
				tweed.enqueue( new Runnable() {
					
					@Override
					public void run() {
						p.set( p.oa1, p.oa2, p.oa3 );
						calculate();
					}});
			}
		} );
		
		JButton depth = new JButton( "depth" );
		depth.addActionListener( new ActionListener() {

			@Override
			public void actionPerformed( ActionEvent e ) {
				Point3d worldPos = new Point3d();
				Vector3d worldNormal = new Vector3d();

				BufferedImage im = p.getRenderPano();
				
				List<float[]> cubes = new ArrayList();
				for ( double a = 0; a < 2 * Math.PI; a += 0.02 )
					for ( double b = -Math.PI / 2; b < Math.PI / 2; b += 0.02 ) {

						Point3d pt = new Point3d( Math.cos( b ) * Math.cos( a ), Math.sin( b ), Math.cos( b ) * Math.sin( a ) );
						pt.scale( 10 );
						pt.add( p.location );

						Color c = new Color ( p.castTo( new float[] { (float) pt.x, (float) pt.y, (float) pt.z }, im, worldPos, worldNormal ) );

						if ( !Double.isNaN( worldPos.x ) )
							cubes.add( new float[] { (float) worldPos.x, (float) worldPos.y, (float) worldPos.z, 
									c.getRed()   / 255f, 
									c.getGreen() / 255f, 
									c.getBlue()  / 255f } );

					}
				addCubes( cubes );
			}
		} );
		
//		JButton render = new JButton("render");
//		render.addActionListener(new ActionListener() {
//			
//			@Override
//			public void actionPerformed(ActionEvent e) {
//				TexGen tg = (TexGen) children.get(0);
//				if (tg != null) {
//					tg.renderTexture( new File(p.orig.getPath()+".projected.png"));
//				}
//			}
//		});
		
		JButton view = new JButton("view");
		view.addActionListener(new ActionListener() {
			
			@Override
			public void actionPerformed(ActionEvent e) {
				tweed.enqueue(new Runnable() {
					@Override
					public void run() {
						tweed.getCamera().setLocation(p.geom.getWorldTranslation());
						tweed.gainFocus();
					}
				} );
			}
		});
		
		JButton background = new JButton("set background");
		background.addActionListener(new ActionListener() {
			
			@Override
			public void actionPerformed(ActionEvent e) {
				
				if (TweedSettings.settings.SSAO) {
					JOptionPane.showMessageDialog( tweed.frame.frame, "Disable SSAO (in the settings menu), then restart." );
					return;
				}
				
				
				tweed.enqueue(new Runnable() {
					@Override
					public void run() {
						
						System.out.println("rendering from "+p.orig.getPath() );
						
						int wS = tweed.getCamera().getWidth(),
							hS = tweed.getCamera().getHeight(),
							wC = 1024,
							hC = 1024;//
						
						BufferedImage target = new BufferedImage( wC, hC, BufferedImage.TYPE_3BYTE_BGR);
						p.ensurePano();
						
						for (int x = 0; x < wC; x++)
							for (int y = 0; y < hC; y++) {
								com.jme3.math.Vector3f loc = tweed.getCamera().getWorldCoordinates(new Vector2f(x * wS / wC, y * hS / hC), 1);
								target.setRGB(x, hC-y-1, 
										p.castTo ( new float[] {loc.x, loc.y, loc.z}, p.panoMedium, null, null ) );
							}
						
//						Graphics2D g2 = (Graphics2D) target.getGraphics();
//						g2.setColor (Color.blue);
//						g2.drawLine( target.getWidth()/2, 0, target.getWidth()/2, target.getHeight() );
//						g2.dispose();
						
						tweed.setBackground(target);
					}
				} );
			}
		});
		
		ui.add(name);
		ui.add(view);
//		ui.add(recalc);
		ui.add(background);
//		ui.add(depth);
		
		tweed.frame.setGenUI( ui );
		
		ui.revalidate();
		ui.repaint();
	}

	private void addCubes (List<float[]> cubes) {
		tweed.enqueue( new Runnable() {
			public void run() {
				
				Mesh mesh = new Mesh();
				mesh.setMode( Mode.Points );
				
				Vector3f[] verts = new Vector3f[cubes.size()];
				Vector4f[] cols  = new Vector4f[cubes.size()];
				
				for (int i = 0; i < cubes.size(); i++) {
					verts[ i ] = new com.jme3.math.Vector3f( cubes.get( i )[ 0 ], cubes.get( i )[ 1 ], cubes.get( i )[ 2 ] );
					cols[i] = new Vector4f( cubes.get( i )[ 3 ], cubes.get( i )[ 4 ], cubes.get( i )[ 5 ], 1 );
				}
				
				mesh.setBuffer( VertexBuffer.Type.Position, 3, BufferUtils.createFloatBuffer( verts ) );
				mesh.setBuffer( VertexBuffer.Type.Color   , 4, BufferUtils.createFloatBuffer( cols  ) );
				
				Material mat1 = new Material( tweed.getAssetManager(), "Common/MatDefs/Misc/Unshaded.j3md" );
				mat1.setBoolean( "VertexColor", true );
				Geometry depth = new Geometry( "depth", mesh );
				depth.setMaterial( mat1 );
				
				depth.updateModelBound();
				depth.updateGeometricState();
				
				
				tweed.frame.addGen( new JmeGen( "depth", tweed, depth ), true );
			}
		} );
	}

	public List<Pano> getPanos(){
		return panos;
	}

	@Override
	public void dumpObj(ObjDump dump) {
		Jme3z.dump( dump, gNode, 0 );
	}
	
	
	@Override
	public void onLoad( Tweed tweed ) {
		super.onLoad( tweed );
		configureFromWorkspaceManifest();
		panos = new CopyOnWriteArrayList<>();
		planes = new ArrayList();
		ui = new JPanel();
	}
}
